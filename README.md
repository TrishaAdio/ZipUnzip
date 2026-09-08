# unzipper-bot

Send a zip to the bot; it sends the contents back **in their native format**.
Images return as photos (grouped into albums, not as file attachments), clips as
videos, tracks as audio, GIFs as animations, everything else as documents.

Built on aiogram 3. Handles archives far past the usual 50 MB when paired with a
local Bot API server — the deploy scripts for that are included.

```
⟡ holiday-photos.zip
84.2 MB · 119 files · 312.7 MB

▪ fetch
◈ unpack  │▪▪▪▪▸∙∙∙∙∙│  48% · 40.1 MB · 84.2 MB · 57 of 119
∙ send

IMG_0421.jpg
```

That card is a single message that animates in place while the job runs.

## Why the local API server matters

| | api.telegram.org | local `telegram-bot-api --local` |
|---|---|---|
| archive the bot can download | **20 MB** | 2000 MB |
| file the bot can send back | 50 MB | 2000 MB |
| how the archive is read | streamed over HTTPS | already on disk, read in place |

The 20 MB `getFile` ceiling — not the 50 MB upload one — is what stops a plain
cloud bot from unzipping anything real. In local mode `getFile` returns a
filesystem path, so a 2 GB archive costs no download at all.

## Quick start

```bash
git clone <this repo> && cd unzipper-bot
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # set BOT_TOKEN
.venv/bin/python -m bot
```

With `API_BASE_URL` empty it runs against the cloud API and warns about the
20 MB cap on startup. Requires Python 3.11+.

## Running with a local Bot API server

Get `api_id` / `api_hash` from [my.telegram.org](https://my.telegram.org) first —
they identify the *server*, not your bot.

**Detach the token once.** A token that has talked to api.telegram.org is
refused by a local server until you log it out:

```bash
curl -s "https://api.telegram.org/bot$BOT_TOKEN/logOut"
```

### Docker

```bash
export TELEGRAM_API_ID=123456 TELEGRAM_API_HASH=abcdef...
docker compose -f deploy/docker-compose.yml up -d --build
```

Both containers mount the same volume at `/data`, which is what lets the bot
read the archive the server wrote instead of downloading it.

### systemd

```bash
sudo TELEGRAM_API_ID=123456 TELEGRAM_API_HASH=abcdef... ./deploy/install-bot-api.sh
```

Compiles `tdlib/telegram-bot-api` from source (10–25 min, ~2 GB RAM — add swap on
a 1 GB box), creates a service account, writes the credentials to
`/etc/telegram-bot-api.env` with mode 600, and starts the unit bound to
127.0.0.1. Then set in `.env`:

```ini
API_BASE_URL=http://127.0.0.1:8081
API_DIR=/var/lib/telegram-bot-api
MAX_ARCHIVE_MB=2000
```

`deploy/unzipper-bot.service` runs the bot itself. The bot's user needs read
access to `API_DIR`, so it joins the `telegram-bot-api` group.

Never expose port 8081 publicly: bot tokens travel in its URLs.

## Commands

| | |
|---|---|
| `/pass <secret>` | password for encrypted archives; `/pass` alone clears it |
| `/raw` | toggle: send everything as documents, no media conversion |
| `/help` | limits and usage |

A password can also ride along in the archive's caption: `pass: hunter2`.
ZipCrypto works out of the box; AES needs `pyzipper`, which is in
`requirements.txt`.

## Configuration

Everything is environment driven, see [.env.example](.env.example) for the full
list with comments. The ones worth knowing:

| variable | default | meaning |
|---|---|---|
| `API_BASE_URL` | *(empty)* | local Bot API endpoint; empty means cloud |
| `API_DIR` | – | the server's `--dir`, as this process sees it |
| `MAX_ARCHIVE_MB` | 2000 | reject bigger archives up front |
| `MAX_TOTAL_UNPACKED_MB` | 4096 | unpacked-size ceiling |
| `MAX_ENTRIES` | 600 | file-count ceiling |
| `MAX_COMPRESSION_RATIO` | 250 | zip-bomb guard |
| `NORMALIZE_PHOTOS` | true | re-encode oversized images so they stay photos |
| `ANIM_INTERVAL` | 1.8 | seconds between progress-card edits |
| `ALLOWED_USERS` | *(empty)* | whitelist; empty means open |

## What it guards against

- **Zip slip.** Entry names are rebuilt component by component; `..`, absolute
  paths and drive letters are stripped, and the resolved path is verified to sit
  inside the target directory. Symlink entries are dropped.
- **Zip bombs.** The central directory is checked before a single byte is
  written: entry count, total unpacked size, and per-entry compression ratio.
  The running total is enforced again mid-extraction.
- **Decompression bombs in images.** Pillow's pixel guard stays on.
- **Junk names.** Control characters stripped, components truncated, CP437
  filenames decoded properly instead of turning into mojibake.
- **Flood limits.** `RetryAfter` is honoured everywhere; the progress card backs
  off and widens its own interval when Telegram pushes back.

## Media rules

Telegram is picky, so the sender adapts rather than failing:

- A photo must be ≤10 MB with width + height ≤10000 and a side ratio ≤20:1.
  Anything outside that is re-encoded to a progressive JPEG (max side 4096,
  quality stepped down until it fits). If it still cannot be a photo — panorama
  strips, HEIC without `pillow-heif` — it goes out as a document instead of
  being dropped.
- Photos and videos share albums; audio and documents get their own; animations
  are always sent alone. Batches of up to 10 preserve archive order.
- Anything Telegram rejects as typed media is retried as a document.
- File type comes from the extension, and from magic bytes when the extension is
  missing or lying.

## Layout

```
bot/
  __main__.py         entrypoint, session wiring, startup checks
  config.py           env parsing
  logs.py             colorama formatter + startup banner
  state.py            per-user password / mode, concurrency gates
  core/
    fetch.py          local-path vs. HTTPS download
    archive.py        inspect, extract, path hardening
    classify.py       extension + magic byte routing
    photos.py         Telegram photo constraints
    sender.py         album batching and fallbacks
    humanize.py       sizes, durations, natural sort
  ui/
    symbols.py        the glyph allowlist
    render.py         every user-facing string
    progress.py       the animating card
deploy/
  docker-compose.yml  bot + telegram-bot-api sharing one volume
  Dockerfile
  install-bot-api.sh  source build + systemd unit
  unzipper-bot.service
scripts/
  selftest.py         offline end-to-end check, no token needed
  check_symbols.py    fails on emoji, italics, fake small-caps
```

## Development

```bash
.venv/bin/python scripts/selftest.py        # builds real zips, unpacks, sends to a stub bot
.venv/bin/python scripts/check_symbols.py   # UI text lint
.venv/bin/python -m ruff check bot scripts
```

`selftest.py` covers zip slip, both size limits, classification by magic bytes,
album grouping, password paths, and that the card stops editing when nothing
changed.

### House style for output

User-facing text uses `bot/ui/symbols.py` and nothing else: `▪` done, `◈`
running, `∙` pending, `✕` failed, `▸` list item, `⟡` one header accent. No
emoji, no italics, no unicode small-caps — `check_symbols.py` fails the build on
all three. Logs are colorama-formatted in fixed columns so severity and source
are scannable.
