# inquiry-relay

**form → anti-spam → IM push.** Self-hosted inquiry receiver for indie sites:
one form endpoint, three anti-spam layers, real-time IM webhook push with
automatic retry. Single process, SQLite only, zero external services.

Part of [aaron-lab](https://github.com/youjunhui-sky/aaron-lab) — Issue #001
has the full build plan and daily logs.

## Features

- `POST /inquiry` — accepts JSON **and** form-encoded submissions
- Anti-spam, in order: **honeypot** (silent bot trap) → **rate limit** (per-IP + per-email) → **Cloudflare Turnstile** (optional, fails closed)
- **Field mapping** — map your form's field names to canonical ones via config
- **Duplicate marking** — same email+message within 24h → `duplicate`, no re-push
- **IM push** — Feishu custom bot (preferred) / Telegram / Slack
- **Retry queue** — push failures retry with exponential backoff (5/10/20/40/80s, max 5), auto-recovers after network restores
- `/healthz` — liveness + DB + queue stats

## Deploy (≤5 minutes)

Needs: Linux + Python 3.10+ + git. Root optional (for the systemd service).

```bash
git clone https://github.com/youjunhui-sky/aaron-lab.git
cd aaron-lab/inquiry-relay
sudo ./install.sh                 # venv + deps + self-test + systemd service
```

Then the only required edit — your IM webhook
(don't have one yet? see **[Get a webhook](#get-a-webhook)** below):

```bash
sudo nano /opt/inquiry-relay/.env         # set INQUIRY_FEISHU_WEBHOOK_URL=...
sudo systemctl restart inquiry-relay
curl localhost:8000/healthz               # {"ok":true,"db":true,...}
```

## Get a webhook

No IM webhook yet? Pick a channel, ~2 minutes each:

**Feishu / Lark** (preferred)
1. Open the target group → ⚙ Settings → Bots (群机器人) → Add Robot → **Custom Bot**
2. Name it, confirm, copy the webhook URL (`https://open.feishu.cn/open-apis/bot/v2/hook/...`)
3. Put the **full URL** into `INQUIRY_FEISHU_WEBHOOK_URL`

**Telegram**
1. Message **@BotFather** → `/newbot` → copy the bot token
2. Add the bot to your group, then find the chat id: `curl https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id":...}`
3. Set `INQUIRY_TELEGRAM_BOT_TOKEN` + `INQUIRY_TELEGRAM_CHAT_ID`

**Slack**
1. Create a Slack app → enable **Incoming Webhooks** → add a webhook for your channel
2. Put the URL into `INQUIRY_SLACK_WEBHOOK_URL`

All channels empty? The service still accepts and stores inquiries
(`status: "received"`) — it just won't push anywhere until you configure one.

Prefer running by hand (no root / no systemd)? Use `--no-service` and run:

```bash
./install.sh --no-service
cd /opt/inquiry-relay   # or wherever INSTALL_DIR points
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Try it

```bash
curl -s localhost:8000/inquiry -H 'Content-Type: application/json' \
  -d '{"name":"Aaron","email":"aaron@example.com","message":"Hello, I want a quote","lang":"en","source_page":"https://example.com/pricing"}'
# → {"ok":true,"id":"...","status":"received"}  and your Feishu group gets a message
```

Rejected paths:

```bash
# 6th submission within a minute from one IP → 429
# honeypot field "website" filled → 202 (bot fooled) but stored as rejected_honeypot
# same email+message within 24h → status:"duplicate", no second push
```

## Configuration

Copy `.env.example` → `.env`; everything is optional, defaults in comments.
Key knobs:

| Variable | Default | Purpose |
|---|---|---|
| `INQUIRY_DB_PATH` | `data/inquiries.db` | SQLite location |
| `INQUIRY_HONEYPOT_FIELD` | `website` | field bots can't resist filling |
| `INQUIRY_RATE_LIMIT_MAX` | `5` | submissions per window (0 = off) |
| `INQUIRY_RATE_LIMIT_WINDOW_SECONDS` | `60` | window length |
| `INQUIRY_TURNSTILE_SECRET_KEY` | *(empty=off)* | Cloudflare Turnstile secret |
| `INQUIRY_FIELD_MAP` | *(empty)* | JSON `{"email":"mail","message":"body"}` |
| `INQUIRY_DEDUPE_WINDOW_SECONDS` | `86400` | duplicate window (24h) |
| `INQUIRY_FEISHU_WEBHOOK_URL` | *(empty)* | Feishu custom bot webhook |
| `INQUIRY_TELEGRAM_BOT_TOKEN` / `INQUIRY_TELEGRAM_CHAT_ID` | *(empty)* | Telegram push |
| `INQUIRY_SLACK_WEBHOOK_URL` | *(empty)* | Slack push |
| `INQUIRY_PUSH_DUPLICATES` | `false` | announce duplicates too |
| `INQUIRY_QUEUE_POLL_SECONDS` | `5` | retry worker tick |

Secrets live only in `.env` (gitignored). Never commit real values.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest -q        # 41 tests
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/inquiry` | receive inquiry (JSON / form-encoded) |
| GET | `/healthz` | `{"ok":true,"db":true,"queue":{...}}` |

Statuses stored per submission: `received` / `duplicate` / `rejected_honeypot`.

## License

MIT
