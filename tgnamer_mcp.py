#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "telethon>=1.40", "anthropic>=0.80", "pydantic>=2"]
# ///
"""MCP server over tgnamer.py: batch username check and claim for Telegram.

Client config (env TG_API_ID, TG_API_HASH, TG_SESSION as for the CLI):
  {"tgnamer": {"command": "uv", "args": ["run", "/path/to/tgnamer_mcp.py"], "env": {...}}}
"""
import asyncio
import os

from mcp.server.fastmcp import FastMCP

import tgnamer

mcp = FastMCP("tgnamer")
MAX_NAMES = 100      # per call; keeps one call under typical MCP client timeouts
MTPROTO_BUDGET = 60  # seconds of Telegram checks per call before returning what is done
_cache: dict[tuple[str | None, str], str] = {}  # (channel, name) -> status


async def in_thread(fn, *args):
    """telethon.sync needs its own loop; the server's loop is busy serving the request."""
    def run():
        asyncio.set_event_loop(asyncio.new_event_loop())
        return fn(*args)
    return await asyncio.to_thread(run)


@mcp.tool()
async def check_usernames(names: list[str], channel: str | None = None) -> dict:
    """Check up to 100 Telegram usernames in one call.

    Returns {"results": {name: status}, "pending": [...], "retry_after": seconds|null, "warning": ...}.
    Statuses: free, taken, purchasable (buyable on Fragment), invalid:<reason> (too_short, bad_chars,
    reserved, ...), for_sale:<TON price>, auction:<TON price>, sold:<TON price>, error.
    Cheap fragment.com + t.me prefilter first; every name that still looks free is confirmed with
    MTProto checkUsername, so reserved names come back invalid:reserved. If Telegram imposes a flood
    wait longer than the call budget, unchecked names are returned in "pending" with "retry_after".
    channel: id, @username or exact title to check on behalf of that channel (see list_channels);
    omitted = personal-account check. Names already checked in this session are served from cache.
    """
    names = [n.lower().lstrip("@") for n in dict.fromkeys(names)]
    out: dict = {"results": {n: _cache[channel, n] for n in names if (channel, n) in _cache}, "pending": [], "retry_after": None}
    todo = [n for n in names if (channel, n) not in _cache]
    if len(todo) > MAX_NAMES:
        out["pending"], todo = todo[MAX_NAMES:], todo[:MAX_NAMES]
    if not os.environ.get("TG_API_ID"):
        out["warning"] = "TG_API_ID not set: 'free' is an HTTP guess, reserved names are not detected"
    try:
        res, pending, retry = await in_thread(tgnamer.check_many, todo, channel, 5, MTPROTO_BUDGET)
    except (RuntimeError, ValueError) as e:
        return {"error": str(e)}
    out["results"].update(res)
    out["pending"] = pending + out["pending"]
    out["retry_after"] = retry
    _cache.update({(channel, n): s for n, s in res.items() if s != "error"})
    return out


@mcp.tool()
async def auth_status() -> dict:
    """Which Telegram account the server is logged in as, or what is missing to log in."""
    try:
        return {"ok": True, **await in_thread(tgnamer.whoami)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def list_channels() -> list[dict] | dict:
    """Channels this account administers: id, title, current username. Use id or title as `channel`."""
    try:
        return await in_thread(tgnamer.list_channels)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def claim_username(name: str, channel: str | None = None, confirm: bool = False) -> str:
    """Set the public link t.me/<name> on a channel, or on the account itself when channel is omitted.

    Replaces the previous username, which anyone can then take: pass confirm=true after the user agreed.
    Re-checks availability first; returns "taken" instead of failing on a race.
    """
    if not confirm:
        return "refused: call again with confirm=true once the user has agreed to change the username"
    try:
        return await in_thread(tgnamer.claim, channel, name)
    except (RuntimeError, ValueError) as e:
        return f"error: {e}"


if __name__ == "__main__":
    mcp.run()
