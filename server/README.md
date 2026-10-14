# kidctl – Server

FastAPI-App mit Eltern-WebUI und Agent-API (Kopplung + WebSocket).

## Betrieb (Docker/Traefik)

Ausführlich: [`docs/installation.md`](../docs/installation.md).

1. `mkdir -p data && sudo chown 10001:10001 data` (Container läuft als UID 10001);
   `.env` aus `.env.example` anlegen, `SECRET_KEY` setzen.
2. In `docker-compose.yml` Traefik-Netzwerknamen und Host prüfen.
3. `docker compose up -d --build`
4. Optional Web Push: `docker compose run --rm kidctl python -m app.cli vapid-keys` → Ausgabe in
   `.env` eintragen, `VAPID_SUBJECT` setzen, `docker compose up -d`.
5. Ersten Eltern-Account anlegen:
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
`report_users`, Chat: `chat_send`, `extension_request`, `chat_sync` (Agent → Server),
`ack`, `chat_message` (Server → Agent). Policy & Nutzung: `policy`, `usage_update`
(Server → Agent), `policy_ack`, `usage_report` (Agent → Server). Close-Codes: 4401 Token ungültig, 4403 widerrufen, 4400 Protokollfehler,
4409 durch neuere Verbindung ersetzt.
