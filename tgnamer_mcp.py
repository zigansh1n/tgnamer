#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "telethon>=1.40", "anthropic>=0.80", "pydantic>=2"]
# ///
"""MCP server over tgnamer.py: batch username check and claim for a Telegram channel.

Client config (env TG_API_ID, TG_API_HASH, TG_SESSION as for the CLI):
  {"tgnamer": {"command": "uv", "args": ["run", "/path/to/tgnamer_mcp.py"], "env": {...}}}
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from mcp.server.fastmcp import FastMCP

import tgnamer

mcp = FastMCP("tgnamer")


async def in_thread(fn, *args):
    """telethon.sync needs its own loop; the server's loop is busy serving the request."""
    def run():
        asyncio.set_event_loop(asyncio.new_event_loop())
        return fn(*args)
    return await asyncio.to_thread(run)


@mcp.tool()
async def check_usernames(names: list[str], channel: str | None = None) -> dict[str, str]:
    """Check many Telegram usernames at once. Returns name -> free | taken | purchasable | invalid | fragment:<status>.

    Cheap fragment.com + t.me prefilter first; every name that still looks free is confirmed with
    MTProto checkUsername (needs TG_API_ID/TG_API_HASH/TG_SESSION), so reserved names come back invalid.
    channel: id, @username or exact title to check on behalf of that channel; omitted = account check.
    """
    with ThreadPoolExecutor(4) as ex:
        out = dict(zip(names, await asyncio.to_thread(lambda: list(ex.map(tgnamer.status, names)))))
    maybe = [n for n, st in out.items() if st in ("free", "error")]
    if not os.environ.get("TG_API_ID"):
        out["_warning"] = "TG_API_ID not set: 'free' is an HTTP guess, reserved names are not detected"
    elif maybe:
        out.update(await in_thread(tgnamer.confirm_mtproto, maybe, channel))
    return out


@mcp.tool()
async def claim_username(channel: str, name: str, confirm: bool = False) -> str:
    """Set the channel's public link to t.me/<name>. Irreversible for the previous name: pass confirm=true.

    channel: id, @username or exact title of a channel this account administers.
    """
    if not confirm:
        return "refused: call again with confirm=true to change the channel's username"
    return await in_thread(tgnamer.claim, channel, name)


if __name__ == "__main__":
    mcp.run()
