# kidctl – Server

FastAPI-App mit Eltern-WebUI und Agent-API (Kopplung + WebSocket).

## Betrieb (Docker/Traefik)

1. `.env` aus `.env.example` anlegen, `SECRET_KEY` setzen.
2. In `docker-compose.yml` Traefik-Netzwerknamen und Host prüfen.
3. `docker compose up -d --build`
4. Ersten Eltern-Account anlegen:
   `docker compose exec kidctl python -m app.cli create-parent`
   (TOTP-QR-Code wird im Terminal angezeigt.) Weitere Accounts im WebUI unter „Eltern“.

Daten (SQLite) liegen unter `./data` (Bind-Mount) – extern sichern.

## Entwicklung

```bash
cd server
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
export KIDCTL_DEV=1 KIDCTL_DATA_DIR=./data BASE_URL=http://127.0.0.1:8000 PYTHONPATH=$PWD:$PWD/../common
.venv/bin/python -m app.cli create-parent
.venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m pytest -q tests
```

## Agent-API (Protokoll v1, siehe `common/kidctl_common/protocol.py`)

| Endpunkt | Zweck |
|---|---|
| `POST /api/v1/enroll` `{code, hostname}` | Kopplungscode → `{device_id, token}` |
| `WS /api/v1/ws` mit `Authorization: Bearer <token>` | Dauerverbindung |

Handshake: Agent sendet `hello {protocol_version, agent_version, hostname}`, Server antwortet
`welcome` bzw. `error unsupported_version` + Close 4426. Weitere Nachrichten: `ping`/`pong`,
`report_users`. Close-Codes: 4401 Token ungültig, 4403 widerrufen, 4400 Protokollfehler,
4409 durch neuere Verbindung ersetzt.
