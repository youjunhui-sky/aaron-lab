# inquiry-relay

**form → anti-spam → IM push.** Self-hosted inquiry receiver for indie sites:
one form endpoint, three anti-spam layers, real-time IM webhook push.
Single container, SQLite only, zero external services.

Part of [aaron-lab](https://github.com/youjunhui-sky/aaron-lab) — see Issue #001
for the build plan and daily logs.

## Status

🚧 D1 (Mon): skeleton + `POST /inquiry` + SQLite persistence.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Submit a test inquiry:

```bash
curl -s localhost:8000/inquiry -H 'Content-Type: application/json' \
  -d '{"name":"Aaron","email":"aaron@example.com","message":"Hello, I want a quote","lang":"en","source_page":"https://example.com/pricing"}'
```

Run tests:

```bash
pip install -r requirements-dev.txt
pytest -q
```

## API (MVP scope)

| Method | Path      | Purpose                                    |
|--------|-----------|--------------------------------------------|
| POST   | /inquiry  | Receive inquiry (JSON or form-encoded)     |
| GET    | /healthz  | Liveness + DB check                        |

## Configuration

All via environment variables (see `.env.example`). Full table lands with the
D2/D3 features they control.

## License

MIT
