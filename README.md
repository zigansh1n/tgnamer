# tgnamer

Find a Telegram username that is actually free.

Generates candidates with Claude from a theme and length, filters them through
fragment.com and t.me for free, then asks Telegram itself via MTProto
`checkUsername` — the same call the official app makes in *Settings → Username*.
Ships as a CLI and as an MCP server, so an AI agent can check a whole list in
one tool call and claim the winner.

```
$ uv run tgnamer.py --theme "telegram fork" --length 5-6 --want 3

fragment:taken     tgram
fragment:available tgweb
invalid            tgapp        ← reserved by Telegram, looks free on t.me
free               tgplx
free               tgpull

FREE: tgplx tgpull
```

## Why another checker

Every public checker on GitHub scrapes t.me or fragment.com. Both say
"available" for names Telegram has reserved, and the moment you type one into
the app you get *Invalid username*. The only authoritative answer comes from
`account.checkUsername` / `channels.checkUsername`, which need a logged-in
account. tgnamer uses the scrapers as a cheap prefilter and MTProto as the
verdict.

| Layer | Needs | Catches |
|---|---|---|
| fragment.com | nothing | collectible names: on auction, for sale, sold |
| t.me | nothing | names with a public profile page |
| MTProto `checkUsername` | API id/hash + session | reserved words, recently released names, purchasable names |

## Install

Requires [uv](https://docs.astral.sh/uv/). Dependencies are declared inline;
the first run installs them.

```bash
git clone https://github.com/zigansh1n/tgnamer && cd tgnamer
```

Environment:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | candidate generation (skip it and pass `--check`) |
| `TG_API_ID`, `TG_API_HASH` | from https://my.telegram.org/apps, enables the MTProto verdict |
| `TG_SESSION` | optional Telethon `StringSession`; without it the first run logs in by phone and saves `tgnamer.session` |

Without `TG_API_ID` the tool still runs, but `free` is only a guess.

## CLI

```bash
# generate + check
uv run tgnamer.py --theme "cyberpunk cat" --length 5-8 --count 40 --rounds 3 --want 5

# check your own list, no AI
uv run tgnamer.py --check durov tgapp tgplx

# check on behalf of a channel (id, @username or exact title)
uv run tgnamer.py --channel "My Channel" --check tgplx

# set the channel's public link
uv run tgnamer.py --channel "My Channel" --claim tgplx
```

Statuses: `free`, `taken`, `purchasable` (buyable on Fragment), `invalid`
(reserved or malformed), `fragment:<status>` (on auction / for sale / sold),
`error`.

`--channel` uses `channels.checkUsername`. If the account already owns the
maximum number of public channels Telegram refuses even to check, and tgnamer
falls back to `account.checkUsername` with a note on stderr.

## MCP server

```bash
claude mcp add tgnamer \
  -e TG_API_ID=... -e TG_API_HASH=... -e TG_SESSION=... \
  -- uv run /path/to/tgnamer_mcp.py
```

Tools:

- `check_usernames(names, channel?)` → `{name: status}` for the whole list in one call.
- `claim_username(channel, name, confirm=false)` → sets the channel's username. Refuses unless `confirm=true`.

Any MCP client works; the config shape is `{"command": "uv", "args": ["run", "/path/to/tgnamer_mcp.py"], "env": {...}}`.

## Notes

- Telegram usernames are 5–32 characters; anything shorter exists only as a Fragment collectible.
- MTProto checks run sequentially and honour `FLOOD_WAIT`. The HTTP prefilter runs in parallel.
- Reserved lists for personal and channel usernames matched on every name tested so far, but the account fallback is not a guarantee for channels.
- Prior art: [jnoc/telegram-username-checker](https://github.com/jnoc/telegram-username-checker) (Telethon, single names), [KiaroSama/Telegram-mcp](https://github.com/KiaroSama/Telegram-mcp) (MCP with `check_channel_username`), [fxcksh/telegram-username-checker](https://github.com/fxcksh/telegram-username-checker) (Fragment API).

## License

MIT
