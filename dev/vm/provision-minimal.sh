#!/usr/bin/env bash
# Minimal-Provisionierung (Debian 13 / Ubuntu 26.04 ohne Oberfläche): nur Paketquellen aktualisieren.
# Dient dem Kurztest der .deb-Pakete (Abhängigkeiten, Agent-Start). Ohne --apply nur Dry-Run.
set -euo pipefail
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
MARKER=/var/lib/kidctl-vm/provisioned
run() { if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi; }
[[ -f "$MARKER" ]] && { echo "Bereits provisioniert."; exit 0; }
export DEBIAN_FRONTEND=noninteractive
run apt-get update
run mkdir -p "$(dirname "$MARKER")"
run touch "$MARKER"
echo "== Minimal-Provisionierung abgeschlossen =="
