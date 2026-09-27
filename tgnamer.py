#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["anthropic>=0.80", "pydantic>=2", "telethon>=1.40"]
# ///
"""Generate Telegram username ideas with Claude and keep only the free ones.

Two check layers:
  1. HTTP prefilter (fragment.com + t.me), no account needed. Heuristic: reserved and
     recently released names look free here.
  2. MTProto account.checkUsername, the same call the official client makes in
     Settings > Username. Authoritative. Needs TG_API_ID / TG_API_HASH from
     https://my.telegram.org/apps and a one-time phone login (session saved next to the script),
     or TG_SESSION with a Telethon StringSession to reuse an existing login.

Usage:
  ANTHROPIC_API_KEY=... TG_API_ID=123 TG_API_HASH=abc uv run tgnamer.py --theme "cyberpunk cat" --length 5-8
  uv run tgnamer.py --check durov some_name   # only check, no generation
"""
import argparse
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import anthropic
from pydantic import BaseModel

VALID = re.compile(r"^[a-z][a-z0-9_]{3,30}[a-z0-9]$")
UA = {"User-Agent": "Mozilla/5.0"}


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
    """free | taken | fragment:<status> | invalid | error"""
    name = name.lower().lstrip("@")
    if not VALID.match(name) or "__" in name:
        return "invalid"
    code, body = _get(f"https://fragment.com/username/{name}")
    if code == 200 and f"<title>{name}" in body.lower():  # 302 -> homepage means not a collectible
        m = re.search(r'tm-section-header-status[^>]*>([^<]+)', body)
        return "fragment:" + (m.group(1).strip().lower() if m else "listed")
    code, body = _get(f"https://t.me/{name}")
    if code != 200:
        return "error"
    # t.me answers 200 for everything; only existing accounts render a page title
    return "taken" if "tgme_page_title" in body else "free"


def tg_client():
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    session = StringSession(os.environ["TG_SESSION"]) if os.environ.get("TG_SESSION") else "tgnamer"
    return TelegramClient(session, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])


def resolve_channel(client, channel: str):
    """channel is a numeric id, @username, or exact dialog title."""
    if channel.startswith("@"):
        return client.get_entity(channel)
    key = str(abs(int(channel))).removeprefix("100") if channel.lstrip("-").isdigit() else channel.lower()
    for d in client.get_dialogs():
        if d.is_channel and (str(d.entity.id) == key or d.title.lower() == key):
            return d.entity
    raise ValueError(f"channel {channel!r} not among this account's dialogs")


def claim(channel: str, name: str) -> str:
    """Set a channel's public username. Re-checks first so a race shows up as 'taken', not an exception."""
    from telethon import functions

    with tg_client() as client:
        ent = resolve_channel(client, channel)
        if not client(functions.channels.CheckUsernameRequest(ent, name)):
            return "taken"
        client(functions.channels.UpdateUsernameRequest(ent, name))
        return f"ok https://t.me/{name}"


def confirm_mtproto(names: list[str], channel: str | None) -> dict[str, str]:
    """free | taken | purchasable | invalid, from Telegram itself. Sequential: FLOOD_WAIT is per account.

    Channel and personal usernames have different reserved lists, so pass --channel for a channel."""
    from telethon import errors, functions

    out: dict[str, str] = {}
    with tg_client() as client:
        if channel:
            ent = resolve_channel(client, channel)
            req = lambda n: functions.channels.CheckUsernameRequest(ent, n)
        else:
            req = functions.account.CheckUsernameRequest
        for n in names:
            while True:
                try:
                    out[n] = "free" if client(req(n)) else "taken"
                except errors.UsernamePurchaseAvailableError:
                    out[n] = "purchasable"
                except errors.UsernameInvalidError:
                    out[n] = "invalid"
                except errors.ChannelsAdminPublicTooMuchError:
                    # account owns the max number of public channels; Telegram refuses even to check
                    print("too many public channels on this account, falling back to account.checkUsername", file=sys.stderr)
                    req = functions.account.CheckUsernameRequest
                    continue
                except errors.FloodWaitError as e:
                    print(f"flood wait {e.seconds}s", file=sys.stderr)
                    time.sleep(e.seconds)
                    continue
                break
            time.sleep(0.3)
    return out


class Ideas(BaseModel):
    usernames: list[str]


def generate(theme: str, length: str, count: int, avoid: set[str]) -> list[str]:
    lo, hi = map(int, length.split("-"))
    client = anthropic.Anthropic()
    prompt = (
        f"Suggest {count} Telegram usernames. Theme/style: {theme}. "
        f"Length {lo}-{hi} characters, lowercase latin letters, digits and single underscores only, "
        f"must start with a letter. Prefer short, memorable, real-word based names; "
        f"avoid random letter soup. Do not repeat: {', '.join(sorted(avoid)) or 'none'}."
    )
    r = client.messages.parse(
        model="claude-opus-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        output_format=Ideas,
    )
    return [u.lower().lstrip("@") for u in r.parsed_output.usernames if lo <= len(u.lstrip("@")) <= hi]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--theme", default="short tech-flavoured handle")
    p.add_argument("--length", default="5-8", help="min-max, e.g. 5-8")
    p.add_argument("--count", type=int, default=30, help="candidates per round")
    p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--want", type=int, default=5, help="stop after this many free names")
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--check", nargs="*", help="skip AI, check these names")
    p.add_argument("--channel", help="channel id, @username or title: check with channels.checkUsername instead of account")
    p.add_argument("--claim", metavar="NAME", help="set --channel's username to NAME and exit")
    a = p.parse_args()
    if a.claim:
        print(claim(a.channel, a.claim))
        return

    seen: set[str] = set()
    free: list[str] = []
    for rnd in range(a.rounds):
        names = a.check if a.check else generate(a.theme, a.length, a.count, seen)
        names = [n for n in dict.fromkeys(names) if n not in seen]
        seen.update(names)
        with ThreadPoolExecutor(a.workers) as ex:
            http = dict(zip(names, ex.map(status, names)))
        maybe = [n for n, st in http.items() if st in ("free", "error")]
        if maybe and os.environ.get("TG_API_ID"):
            http.update(confirm_mtproto(maybe, a.channel))
        for name, st in http.items():
            print(f"{st:18} {name}", flush=True)
            if st == "free":
                free.append(name)
        if a.check or len(free) >= a.want:
            break
        time.sleep(1)  # ponytail: fixed pause, no backoff; add if t.me starts throttling
    print("\nFREE:", " ".join(free) or "none", file=sys.stderr)


if __name__ == "__main__":
    main()
