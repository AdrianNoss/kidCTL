#!/usr/bin/env bash
# Entwicklungsserver für Tests mit der VM (nur lokal, 127.0.0.1:8000; aus der VM: 10.0.2.2:8000).
# Daten bleiben über Neustarts erhalten: ~/.local/share/kidctl-dev (KIDCTL_DEV_DIR).
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
