#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["anthropic>=0.80", "pydantic>=2", "telethon>=1.40"]
# ///
"""Find a Telegram username that is actually free.

Two check layers:
  1. HTTP prefilter (fragment.com + t.me), no account needed. Heuristic: reserved and
     recently released names look free here.
  2. MTProto checkUsername, the same call the official client makes in Settings > Username.
     Authoritative. Needs TG_API_ID / TG_API_HASH from https://my.telegram.org/apps and a
     session: run `--login` once and export the printed TG_SESSION.

Usage:
  uv run tgnamer.py --login
  uv run tgnamer.py --theme "cyberpunk cat" --length 5-8      # needs ANTHROPIC_API_KEY
  uv run tgnamer.py --check durov some_name                   # no AI, just check
  uv run tgnamer.py --channel "My Channel" --claim some_name
"""
import argparse
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0"}
FRAGMENT_STATUS = {"tm-status-taken": "taken", "tm-status-avail": "for_sale", "tm-status-unavail": "sold"}


def validate(name: str) -> str | None:
    """Telegram's form rules; anything else (reserved words, released names) only the server knows."""
    if len(name) < 5:
        return "too_short"
    if len(name) > 32:
        return "too_long"
    if not re.fullmatch(r"[a-z0-9_]+", name):
        return "bad_chars"
    if name[0].isdigit():
        return "starts_with_digit"
    if "__" in name or name.endswith("_"):
        return "bad_underscore"
    return None


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (OSError, TimeoutError):
        return 0, ""


def status(name: str) -> str:
    """free | taken | invalid:<reason> | for_sale:<ton> | auction:<ton> | sold:<ton> | error

    'free' here is a guess; confirm_mtproto() gives the verdict."""
    name = name.lower().lstrip("@")
    if reason := validate(name):
        return f"invalid:{reason}"
    code, body = _get(f"https://fragment.com/username/{name}")
    if code == 200 and f"<title>{name}" in body.lower():  # 302 -> homepage means not a collectible
        m = re.search(r'tm-section-header-status (tm-status-\w+)">([^<]*)', body)
        cls, text = (m.group(1), m.group(2).strip().lower()) if m else ("", "")
        st = FRAGMENT_STATUS.get(cls, "taken")
        if st == "for_sale" and "auction" in text:
            st = "auction"
        price = re.search(r'icon-ton">([\d,]+)', body)
        return st if st == "taken" else f"{st}:{price.group(1).replace(',', '') if price else '?'}"
    code, body = _get(f"https://t.me/{name}")
    if code != 200:
        return "error"
    # t.me answers 200 for everything; only existing accounts render a page title
    return "taken" if "tgme_page_title" in body else "free"


def tg_client():
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    try:
        api_id, api_hash = int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]
    except KeyError as e:
        raise RuntimeError(f"{e.args[0]} is not set; get it at https://my.telegram.org/apps") from None
    try:
        session = StringSession(os.environ["TG_SESSION"]) if os.environ.get("TG_SESSION") else "tgnamer"
    except ValueError:
        raise RuntimeError("TG_SESSION is not a valid Telethon StringSession: run `tgnamer.py --login`") from None
    client = TelegramClient(session, api_id, api_hash)
    client.connect()
    if not client.is_user_authorized():
        client.disconnect()
        raise RuntimeError("Telegram session missing or revoked: run `tgnamer.py --login` and export TG_SESSION")
    return client


def login() -> str:
    """Interactive phone login; prints a StringSession to put into TG_SESSION."""
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    with TelegramClient(StringSession(), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
        client.start()
        return client.session.save()


def whoami() -> dict:
    with tg_client() as client:
        me = client.get_me()
        return {"id": me.id, "username": me.username, "name": f"{me.first_name or ''} {me.last_name or ''}".strip()}


def list_channels() -> list[dict]:
    """Channels this account can administer, for use as `channel` elsewhere."""
    with tg_client() as client:
        return [
            {"id": d.entity.id, "title": d.title, "username": d.entity.username}
            for d in client.get_dialogs()
            if d.is_channel and not getattr(d.entity, "megagroup", False)
            and (getattr(d.entity, "creator", False) or getattr(d.entity, "admin_rights", None))
        ]


def resolve_channel(client, channel: str):
    """channel is a numeric id, @username, or exact dialog title."""
    if channel.startswith("@"):
        return client.get_entity(channel)
    key = str(abs(int(channel))).removeprefix("100") if channel.lstrip("-").isdigit() else channel.lower()
    for d in client.get_dialogs():
        if d.is_channel and (str(d.entity.id) == key or d.title.lower() == key):
            return d.entity
    raise ValueError(f"channel {channel!r} not among this account's dialogs; see --channels")


def claim(channel: str | None, name: str) -> str:
    """Set a channel's (or, with channel=None, the account's) public username.

    Re-checks first so a race shows up as 'taken', not an exception."""
    from telethon import functions

    name = name.lower().lstrip("@")
    if reason := validate(name):
        return f"invalid:{reason}"
    with tg_client() as client:
        if channel:
            ent = resolve_channel(client, channel)
            check, update = (lambda n: functions.channels.CheckUsernameRequest(ent, n),
                             lambda n: functions.channels.UpdateUsernameRequest(ent, n))
        else:
            check, update = functions.account.CheckUsernameRequest, functions.account.UpdateUsernameRequest
        if not client(check(name)):
            return "taken"
        client(update(name))
        return f"ok https://t.me/{name}"


def confirm_mtproto(names: list[str], channel: str | None = None, budget: float | None = None):
    """Verdict from Telegram: free | taken | purchasable | invalid:reserved.

    Sequential, FLOOD_WAIT is per account. With `budget` (seconds) a flood wait longer than what is
    left stops the run: returns (results, pending_names, retry_after_seconds). Without it, sleeps.
    Channel and personal usernames may have different reserved lists, so pass `channel` for a channel."""
    from telethon import errors, functions

    out: dict[str, str] = {}
    deadline = time.monotonic() + budget if budget else None
    with tg_client() as client:
        if channel:
            ent = resolve_channel(client, channel)
            req = lambda n: functions.channels.CheckUsernameRequest(ent, n)
        else:
            req = functions.account.CheckUsernameRequest
        for i, n in enumerate(names):
            while True:
                try:
                    out[n] = "free" if client(req(n)) else "taken"
                except errors.UsernamePurchaseAvailableError:
                    out[n] = "purchasable"
                except errors.UsernameInvalidError:
                    out[n] = "invalid:reserved"
                except errors.ChannelsAdminPublicTooMuchError:
                    # account owns the max number of public channels; Telegram refuses even to check
                    print("too many public channels on this account, falling back to account.checkUsername", file=sys.stderr)
                    req = functions.account.CheckUsernameRequest
                    continue
                except errors.FloodWaitError as e:
                    if deadline and time.monotonic() + e.seconds > deadline:
                        return out, names[i:], e.seconds
                    print(f"flood wait {e.seconds}s", file=sys.stderr)
                    time.sleep(e.seconds)
                    continue
                break
            time.sleep(0.3)
    return out, [], None


def check_many(names: list[str], channel: str | None = None, workers: int = 5, budget: float | None = None):
    """HTTP prefilter in parallel, then MTProto for whatever still looks free. Same return shape as confirm_mtproto."""
    names = [n.lower().lstrip("@") for n in dict.fromkeys(names)]
    with ThreadPoolExecutor(workers) as ex:
        out = dict(zip(names, ex.map(status, names)))
    maybe = [n for n, st in out.items() if st in ("free", "error")]
    if maybe and os.environ.get("TG_API_ID"):
        res, pending, retry = confirm_mtproto(maybe, channel, budget)
        out.update(res)
        return out, pending, retry
    return out, [], None


def generate(theme: str, length: str, count: int, avoid: set[str]) -> list[str]:
    import anthropic
    from pydantic import BaseModel

    class Ideas(BaseModel):
        usernames: list[str]

    lo, hi = map(int, length.split("-"))
    prompt = (
        f"Suggest {count} Telegram usernames. Theme/style: {theme}. "
        f"Length {lo}-{hi} characters, lowercase latin letters, digits and single underscores only, "
        f"must start with a letter. Prefer short, memorable, real-word based names; "
        f"avoid random letter soup. Do not repeat: {', '.join(sorted(avoid)) or 'none'}."
    )
    r = anthropic.Anthropic().messages.parse(
        model="claude-opus-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        output_format=Ideas,
    )
    return [u.lower().lstrip("@") for u in r.parsed_output.usernames if lo <= len(u.lstrip("@")) <= hi]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--theme", default="short tech-flavoured handle")
    p.add_argument("--length", default="5-8", help="min-max, e.g. 5-8")
    p.add_argument("--count", type=int, default=30, help="candidates per round")
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--want", type=int, default=5, help="stop after this many free names")
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--check", nargs="*", help="skip AI, check these names")
    p.add_argument("--channel", help="channel id, @username or title: check/claim on behalf of that channel")
    p.add_argument("--claim", metavar="NAME", help="set the username (of --channel, else of the account) and exit")
    p.add_argument("--login", action="store_true", help="log in by phone and print TG_SESSION")
    p.add_argument("--whoami", action="store_true")
    p.add_argument("--channels", action="store_true", help="list channels you administer")
    a = p.parse_args()
    if a.login:
        print(f"export TG_SESSION={login()}")
        return
    if a.whoami:
        print(whoami())
        return
    if a.channels:
        for c in list_channels():
            print(f"{c['id']:<14} {'@' + c['username'] if c['username'] else '-':<20} {c['title']}")
        return
    if a.claim:
        print(claim(a.channel, a.claim))
        return

    if not os.environ.get("TG_API_ID"):
        print("TG_API_ID not set: 'free' is an HTTP guess, reserved names are not detected", file=sys.stderr)
    seen: set[str] = set()
    free: list[str] = []
    for _ in range(a.rounds):
        names = a.check if a.check else generate(a.theme, a.length, a.count, seen)
        names = [n for n in dict.fromkeys(names) if n not in seen]
        seen.update(names)
        results, _, _ = check_many(names, a.channel, a.workers)
        for name, st in results.items():
            print(f"{st:18} {name}", flush=True)
            if st == "free":
                free.append(name)
        if a.check or len(free) >= a.want:
            break
        time.sleep(1)  # ponytail: fixed pause, no backoff; add if t.me starts throttling
    print("\nFREE:", " ".join(free) or "none", file=sys.stderr)


if __name__ == "__main__":
    main()
