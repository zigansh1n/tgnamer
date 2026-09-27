#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["telethon>=1.40", "anthropic>=0.80", "pydantic>=2"]
# ///
"""Public Telegram bot: paste usernames, get their status. No user session involved.

Bots cannot call checkUsername, so this is the HTTP layer only: taken / for sale / sold / auction
are certain, reserved names are caught by reserved.txt, and "free" is reported as "looks free".

Env: BOT_TOKEN (from @BotFather), TG_API_ID / TG_API_HASH (MTProto transport for the bot itself, no user login),
optional ANTHROPIC_API_KEY for /gen and GEN_MODEL (default claude-opus-5).
"""
import asyncio
import os
import re
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from telethon import TelegramClient, events

import tgnamer

MAX_PER_MSG = 20
NAMES_PER_HOUR = 100
GENS_PER_DAY = 5
GEN_COUNT = 20
_pool = ThreadPoolExecutor(6)
_names_log: dict[int, deque] = defaultdict(deque)   # user -> timestamps of checked names
_gen_log: dict[int, deque] = defaultdict(deque)

ICON = {"free": "✅", "taken": "❌", "for_sale": "💎", "auction": "🔨", "sold": "🔒", "invalid": "⛔", "error": "⚠️"}
HELP = (
    "Send me usernames (space or newline separated, up to 20) and I'll tell you what's up with each.\n"
    "/gen <theme> [min-max] — let AI suggest names and check them, e.g. /gen cyberpunk cat 5-7\n\n"
    "✅ looks free — nothing on t.me or Fragment; confirm in Telegram → Settings → Username\n"
    "❌ taken · 💎 for sale on Fragment · 🔨 on auction · 🔒 sold · ⛔ reserved or malformed"
)


def _allow(log: dict[int, deque], user: int, limit: int, window: float, n: int = 1) -> bool:
    q = log[user]
    now = time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) + n > limit:
        return False
    q.extend([now] * n)
    return True


def fmt(name: str, st: str) -> str:
    kind, _, detail = st.partition(":")
    icon = ICON.get(kind, "•")
    if kind == "free":
        return f"{icon} {name} — looks free"
    if kind in ("for_sale", "auction", "sold"):
        return f"{icon} {name} — {kind.replace('_', ' ')} on fragment.com/username/{name}, {detail} TON"
    if kind == "invalid":
        return f"{icon} {name} — {detail.replace('_', ' ')}"
    return f"{icon} {name} — {kind}"


async def check(names: list[str]) -> list[str]:
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(*(loop.run_in_executor(_pool, tgnamer.status, n) for n in names))
    return [fmt(n, st) for n, st in zip(names, results)]


client = TelegramClient("tgnamer_bot", int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])


@client.on(events.NewMessage(pattern=r"^/(start|help)$"))
async def start(ev):
    await ev.reply(HELP)


@client.on(events.NewMessage(pattern=r"^/gen(?:\s+(.*))?$"))
async def gen(ev):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return await ev.reply("Generation is off on this bot. Send me your own list of names instead.")
    if not _allow(_gen_log, ev.sender_id, GENS_PER_DAY, 86400):
        return await ev.reply(f"Limit: {GENS_PER_DAY} generations per day. Send your own list, that has no limit.")
    arg = (ev.pattern_match.group(1) or "").strip()
    m = re.search(r"\b(\d{1,2})-(\d{1,2})$", arg)
    length, theme = (m.group(0), arg[: m.start()].strip()) if m else ("5-8", arg)
    theme = theme or "short memorable handle"
    msg = await ev.reply(f"Thinking up {GEN_COUNT} names for “{theme}”…")
    try:
        names = await asyncio.get_running_loop().run_in_executor(_pool, tgnamer.generate, theme, length, GEN_COUNT, set())
    except Exception as e:  # any SDK failure: the user just needs to know it didn't work
        return await msg.edit(f"Generation failed: {type(e).__name__}")
    lines = await check(names[:MAX_PER_MSG])
    await msg.edit("\n".join(lines) or "Nothing generated, try another theme.")


@client.on(events.NewMessage(func=lambda ev: ev.is_private and ev.raw_text and not ev.raw_text.startswith("/")))
async def on_text(ev):
    names = list(dict.fromkeys(t.lower().lstrip("@") for t in re.findall(r"@?[\w]+", ev.raw_text)))[:MAX_PER_MSG]
    if not names:
        return
    if not _allow(_names_log, ev.sender_id, NAMES_PER_HOUR, 3600, len(names)):
        return await ev.reply(f"Limit: {NAMES_PER_HOUR} names per hour. Try again later.")
    await ev.reply("\n".join(await check(names)))


if __name__ == "__main__":
    client.start(bot_token=os.environ["BOT_TOKEN"])
    print("tgnamer bot running as", client.get_me().username)
    client.run_until_disconnected()
