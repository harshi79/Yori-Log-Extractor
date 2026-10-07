# Yori Log Extractor · Telegram → MantisBin

**Yes, it can be a Telegram bot — and this is it.** Send the bot a log (typed text or a `.txt`/`.log` file) and it stores the paste on [MantisBin](https://mantisbin.yorichii.workers.dev) with **no expiry** and replies with the share link.

```
You   ⭣  (paste 200 lines of nginx errors, or attach access.log)
      [ Yori Log Extractor ]
Bot   ⭣  ✅ Pasted to MantisBin
         🔗 https://mantisbin.yorichii.workers.dev/p/a8Kx92Lm
         📄 Yori Logs · access.log · 2026-10-07 19:42 · tg:777
         18.4 KB · plaintext · unlisted
         ⏳ expires: never
         [ 🔗 Open paste ]  [ 🗑 Delete from MantisBin ]
```

The bot is a thin client of the MantisBin JSON API you already documented: one `POST /api/pastes` per paste, one `GET /api/pastes/:id` when it expands a link, one `DELETE /api/pastes/:id` for the delete button. There is no database, no webhook and no inbound port — just long polling and two HTTPS calls.

---

## Quick start

```bash
git clone https://github.com/harshi79/Yori-Log-Extractor
cd Yori-Log-Extractor
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then open .env and paste the two values below
python run.py --check       # verifies the token and the API key, then exits
python run.py               # start the bot
```

### The two values in `.env`

| Value | Where to get it |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Message **[@BotFather](https://t.me/BotFather)** → `/newbot` → pick a name and a username → copy the line that looks like `123456789:AAH…` |
| `MANTISBIN_API_KEY` | Sign in at **https://mantisbin.yorichii.workers.dev/me** → create an API key → copy it immediately (it starts with `mb_` and is shown only once) |

Then talk to your bot: open `https://t.me/<your-bot-username>`, press **Start**, and paste a log.

> The API key belongs to *your* MantisBin account: every paste the bot creates is owned by that account and can be managed from `/me`. Keys are stored as hashes by MantisBin, but anyone holding the key can read and delete everything it created — keep `.env` out of Git (it already is).

---

## What the bot does

| You send | The bot does |
| --- | --- |
| **Text of 500+ characters** | Creates a paste (`language: auto`, so JSON/YAML/SQL/… are detected) and replies with the link |
| **`#paste …`** (any length) | Same, but skips the 500-character floor — for short snippets |
| **A file** (`.log`, `.txt`, `.json`, `.py`, …) | Downloads it, checks it is text, detects the language from the extension, and pastes it (up to `MAX_FILE_MB`) |
| **A file with the caption `#paste`** | Stores it even if it looks binary (last resort for odd encodings) |
| **A message containing a MantisBin link** | Expands it inline: title, size, views, expiry and the first few hundred characters |
| **A photo/voice/video** | Explains that only text and files are supported (photos are never pasted) |

Commands: `/start`, `/help`, `/settings`, `/id`, `/delete <id|link>`. Unknown commands get a nudge towards `/help`.

Handy details:

- **Everything is unlisted** and expires **never** by default (change with `MANTISBIN_EXPIRY` or `/settings`).
- Titles are prefixed (`Yori Logs · access.log · 2026-10-07 19:42 · tg:777`) so bot pastes are easy to spot in `/me`.
- The whole flow happens in **one edited message** — no "uploading…" spam.
- `/delete` and the 🗑 button only remove pastes *this bot* created in that chat, and only for the person who created them.

---

## How it works

```
   Telegram servers            your machine / VPS / container
  ┌────────────────┐          ┌──────────────────────────────────┐
  │  getUpdates    │◀─────────│  run.py  (long polling, no port) │
  │  (your chat)   │─────────▶│                                  │
  └────────────────┘  update  │  bot/handlers.py  → parse/detect │
                              │  bot/mantisbin.py → HTTPS client │
                              └───────────────┬──────────────────┘
                                              │ POST /api/pastes      {"expiresIn":"never"}
                                              │ GET  /api/pastes/:id  (link expansion)
                                              │ DELETE /api/pastes/:id (delete button)
                                              ▼
                               MantisBin  https://mantisbin.yorichii.workers.dev
```

**Why long polling instead of a webhook?** A webhook needs a public HTTPS URL, a TLS certificate, a secret token and a worker for every update — sensible if you want the bot on Cloudflare Workers, but overkill for one user. Long polling needs none of that: the same `python run.py` works on a laptop, a Raspberry Pi or a $4 VPS, and the bot is offline-safe (Telegram queues updates for ~24h and the bot collects them on restart).

---

## Configuration (`.env`)

Only the first two are required.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather |
| `MANTISBIN_API_KEY` | — | `mb_…` key from `/me` |
| `MANTISBIN_BASE_URL` | `https://mantisbin.yorichii.workers.dev` | Point at a self-hosted MantisBin |
| `MANTISBIN_EXPIRY` | `never` | `never`, `10m`, `1h`, `6h`, `1d`, `1w`, `1mo`, `1y` |
| `MANTISBIN_TITLE_PREFIX` | `Yori Logs` | Prefix for every title |
| `BOT_ACCESS` | `anyone` | `anyone` (public bot) or `owner` (private) |
| `BOT_OWNER_ID` | — | Your numeric id (send `/id` to the bot), required for `owner` |
| `BOT_ALLOWED_USERS` | — | Comma separated numeric ids; `any` disables the list. Only applies when `BOT_ACCESS=anyone` |
| `MAX_FILE_MB` | `10` | Refuse bigger files before downloading them |
| `HTTP_TIMEOUT` | `30` | Seconds to wait on a MantisBin request |

**Before you make the bot public**, consider `BOT_ACCESS=owner` (or `BOT_ALLOWED_USERS`): every stranger who finds the bot would otherwise create pastes **in your MantisBin account** (10 MB per paste, 300 per hour on your key).

`/settings` changes expiry and visibility per chat for the current run; restarts reset it to the `.env` defaults.

---

## Run it 24/7

**systemd (VPS / home server)**

```bash
sudo useradd --system --home /opt/yori-log-extractor --shell /usr/sbin/nologin yori
sudo git clone https://github.com/harshi79/Yori-Log-Extractor /opt/yori-log-extractor
sudo python3 -m venv /opt/yori-log-extractor/.venv
sudo /opt/yori-log-extractor/.venv/bin/pip install -r /opt/yori-log-extractor/requirements.txt
sudo cp /opt/yori-log-extractor/.env.example /opt/yori-log-extractor/.env && sudo nano /opt/yori-log-extractor/.env
sudo chown -R yori:yori /opt/yori-log-extractor
sudo cp /opt/yori-log-extractor/deploy/yori-bot.service /etc/systemd/system/
sudo systemctl enable --now yori-bot
journalctl -u yori-bot -f
```

**Docker**

```bash
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f
```

**Raspberry Pi / always-on PC** — `python run.py` in a `tmux`/`screen` session, or the systemd unit above.

---

## Development

```bash
pip install -r requirements-dev.txt
pytest -q          # 118 tests, no network needed (the API is mocked with httpx.MockTransport)
ruff check .       # lint + import order
python run.py -v --check
```

```
bot/
  __main__.py     CLI: banner, --check, --show-config, logging
  app.py          Application wiring, handler registration, polling supervisor
  config.py       environment parsing + friendly validation errors
  handlers.py     the chat behaviour (commands, paste creation, link expansion)
  mantisbin.py    async API client: create / get / delete, error translation
  names.py        titles, language detection, text-vs-binary heuristics
  formatting.py   Telegram-safe HTML rendering, MantisBin link parsing
deploy/yori-bot.service   systemd unit
tests/                     unit + end-to-end handler tests with stubbed Telegram
```

Adding a command: write `async def cmd_x(update, context)` in `bot/handlers.py`, register it in `register_handlers()` (`bot/app.py`), add it to `BOT_COMMANDS` if it should show up in Telegram's menu.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Missing required setting(s)` | No `.env` (or empty values). Copy `.env.example`, fill both lines |
| `Telegram rejected the token` | Copy the whole line from @BotFather, including the digits before the colon |
| `Could not reach Telegram` | `api.telegram.org` must be reachable — no proxy, no firewall block |
| `MantisBin rejected the API key (401)` | The key was deleted or belongs to another instance; make a new one at `/me` |
| `409 Conflict` in the log | Another instance of the bot is polling the same token — stop the other one |
| `Rate limited by MantisBin (429)` | More than 300 creates/hour on the key; the message says how long to wait |
| `Too big for a paste` | MantisBin caps a keyed paste at 10 MB (5 MB anonymous); raise `MAX_FILE_MB` only up to that |
| Bot silent in a group | Groups need privacy mode disabled in @BotFather (`/setprivacy` → Disable) to see plain text |
| Nothing happens for a 200-character message | Text pastes start at 500 characters; use `#paste` to force |

---

## Privacy & security notes

- The bot keeps **nothing** on disk: no database, no log files (stdout only), no copy of your logs. It holds one outbound HTTPS client plus an in-memory per-chat settings dict.
- Pastes are **unlisted** and `noindex`d by MantisBin; anyone with the link can read them, so don't paste secrets you wouldn't share. The link *is* the capability.
- Because the API key acts as its owner, the bot can only manage pastes that key created — it cannot read your other pastes.
- Message HTML is escaped through a whitelist before it is sent, so a log line containing `<script>` can never break or inject markup into the chat message.
- Refer to the [MantisBin API docs](https://mantisbin.yorichii.workers.dev/docs) for server-side behaviour: burn-after-reading, passwords, thumbnails, rate limits.

---

## License

Apache-2.0 — see [LICENSE](LICENSE).
