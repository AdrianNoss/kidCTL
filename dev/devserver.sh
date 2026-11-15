#!/usr/bin/env bash
# Entwicklungsserver für Tests mit der VM (nur lokal, 127.0.0.1:8000; aus der VM: 10.0.2.2:8000).
# Daten bleiben über Neustarts erhalten: ~/.local/share/kidctl-dev (KIDCTL_DEV_DIR), Datenbank im Podman-Container
# kidctl-pg. Frühere SQLite-Daten übernehmen: PYTHONPATH=server:common python -m app.cli import-sqlite \
#   ~/.local/share/kidctl-dev/data/kidctl.db --apply  (mit KIDCTL_DEV=1)
# Beim ersten Start wird der Eltern-Account „mama“ angelegt; das TOTP-Secret liegt in secret.txt
# (für dev/webctl.py).
#
#   dev/devserver.sh            Server im Vordergrund starten
#   dev/devserver.sh setup      nur venv/Account anlegen
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
D="${KIDCTL_DEV_DIR:-$HOME/.local/share/kidctl-dev}"
PY="$D/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  python -m venv "$D/venv"
  "$D/venv/bin/pip" install -q -r "$REPO/server/requirements.txt" pytest httpx dbus-next websockets
fi

# Datenbank: PostgreSQL im Podman-Container kidctl-pg (nur 127.0.0.1:55432), bei Bedarf anlegen bzw. starten
if command -v podman >/dev/null; then
  if ! podman container exists kidctl-pg; then
    podman run -d --name kidctl-pg -p 127.0.0.1:55432:5432 -e POSTGRES_USER=kidctl -e POSTGRES_PASSWORD=kidctl-dev \
      -e POSTGRES_DB=kidctl docker.io/library/postgres:17 >/dev/null
  fi
  podman start kidctl-pg >/dev/null
  until podman exec kidctl-pg pg_isready -U kidctl -d kidctl >/dev/null 2>&1; do sleep 1; done
fi

export KIDCTL_DEV=1 SECRET_KEY=dev-secret KIDCTL_DATA_DIR="$D/data" BASE_URL=http://10.0.2.2:8000 \
       COOKIE_SECURE=0 PYTHONPATH="$REPO/server:$REPO/common"
mkdir -p "$D/data"

if [[ ! -f "$D/secret.txt" ]]; then
  KIDCTL_BOOTSTRAP_USER=mama KIDCTL_BOOTSTRAP_PASSWORD=ein-langes-passwort \
    "$PY" -m app.cli create-parent | grep -oE "Secret: \w+" > "$D/secret.txt"
  echo "Eltern-Account mama angelegt (Passwort: ein-langes-passwort, TOTP-Secret in $D/secret.txt)"
fi

[[ "${1:-}" == "setup" ]] && exit 0
cd "$REPO/server"
exec "$D/venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000
