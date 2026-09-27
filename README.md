# tgnamer

Find a Telegram username that is actually free.

Generates candidates with Claude from a theme and length, filters them through
fragment.com and t.me for free, then asks Telegram itself via MTProto
`checkUsername` — the same call the official app makes in *Settings → Username*.
Ships as a CLI and as an MCP server, so an AI agent can check a whole list in
one tool call and claim the winner.

```
$ uv run tgnamer.py --check tgram tgweb gramv tgapp tgplx

taken              tgram
for_sale:100       tgweb        ← on Fragment, price in TON
auction:2260       gramv
invalid:reserved   tgapp        ← reserved by Telegram, looks free on t.me
free               tgplx

FREE: tgplx
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
| `TG_SESSION` | Telethon `StringSession` printed by `--login` |

Without `TG_API_ID` the tool still runs, but `free` is only a guess and it says so.

```bash
export TG_API_ID=... TG_API_HASH=...
uv run tgnamer.py --login      # phone + code once, prints: export TG_SESSION=...
```

## CLI

```bash
# generate + check
uv run tgnamer.py --theme "cyberpunk cat" --length 5-8 --count 40 --rounds 3 --want 5

# check your own list, no AI
uv run tgnamer.py --check durov tgapp tgplx

# who am I, which channels can I use
uv run tgnamer.py --whoami
uv run tgnamer.py --channels

# check on behalf of a channel (id, @username or exact title)
uv run tgnamer.py --channel "My Channel" --check tgplx

# set the public link: of a channel, or of the account when --channel is omitted
uv run tgnamer.py --channel "My Channel" --claim tgplx
```

Statuses:

| Status | Meaning |
|---|---|
| `free` | Telegram confirmed it can be set |
| `taken` | someone has it |
| `purchasable` | Telegram says it is buyable on Fragment |
| `for_sale:<TON>` / `auction:<TON>` / `sold:<TON>` | Fragment listing with price |
| `invalid:<reason>` | `too_short`, `too_long`, `bad_chars`, `starts_with_digit`, `bad_underscore` (checked locally) or `reserved` (Telegram) |
| `error` | network failure on the HTTP layer |

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

- `check_usernames(names, channel?)` → up to 100 names per call. Returns `{"results": {name: status}, "pending": [...], "retry_after": seconds|null}`; if Telegram imposes a flood wait longer than the 60 s call budget, the rest comes back in `pending` for a later call. Results are cached for the server's lifetime.
- `auth_status()` → which account the session belongs to, or what is missing.
- `list_channels()` → channels you administer, to pick a `channel`.
- `claim_username(name, channel?, confirm=false)` → sets the username on a channel, or on the account when `channel` is omitted. Refuses unless `confirm=true`.

Claude Desktop / Cursor / any MCP client:

```json
{
  "mcpServers": {
    "tgnamer": {
      "command": "uv",
      "args": ["run", "/path/to/tgnamer_mcp.py"],
      "env": {"TG_API_ID": "...", "TG_API_HASH": "...", "TG_SESSION": "..."}
    }
  }
}
```

The agent is the generator: ask it for candidates and let it call `check_usernames` with the whole list. The CLI's Claude call exists for use without an agent.

## Notes

- Telegram usernames are 5–32 characters; anything shorter exists only as a Fragment collectible.
- MTProto checks run sequentially and honour `FLOOD_WAIT`. The HTTP prefilter runs in parallel.
- Reserved lists for personal and channel usernames matched on every name tested so far, but the account fallback is not a guarantee for channels.
- Prior art: [jnoc/telegram-username-checker](https://github.com/jnoc/telegram-username-checker) (Telethon, single names), [KiaroSama/Telegram-mcp](https://github.com/KiaroSama/Telegram-mcp) (MCP with `check_channel_username`), [fxcksh/telegram-username-checker](https://github.com/fxcksh/telegram-username-checker) (Fragment API).

## License

MIT
