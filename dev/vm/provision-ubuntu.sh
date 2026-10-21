#!/usr/bin/env bash
# Provisionierung der Ubuntu-24.04-Test-VM (läuft IN der VM als root, per cloud-init beim ersten Start).
# GNOME (ubuntu-desktop-minimal, GDM, Wayland), deutsche Sprache/Tastatur. Ohne --apply nur Dry-Run.
set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
MARKER=/var/lib/kidctl-vm/provisioned

run() {
  if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi
}

PACKAGES=(
  # Desktop: GNOME wie bei Ubuntu Desktop (minimal), GDM unter Wayland
  ubuntu-desktop-minimal
  language-pack-de language-pack-gnome-de
  # Werkzeuge
  git vim htop less
)

echo "== kidctl-VM-Provisionierung Ubuntu ($(date -Is)) =="
if [[ -f "$MARKER" ]]; then
  echo "Bereits provisioniert ($MARKER) – nichts zu tun."
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
run dpkg --configure -a   # falls ein früherer Lauf unterbrochen wurde
run apt-get update
run apt-get -y full-upgrade
run apt-get -y install "${PACKAGES[@]}"
run locale-gen de_DE.UTF-8 en_US.UTF-8
run update-locale LANG=de_DE.UTF-8
# Tastatur (Konsole, GDM, GNOME) – Debian/Ubuntu: /etc/default/keyboard statt localectl
run sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="de"/' /etc/default/keyboard
run setupcon --save || true
run systemctl set-default graphical.target

run mkdir -p "$(dirname "$MARKER")"
run touch "$MARKER"
echo "== Provisionierung abgeschlossen – Neustart aktiviert GDM =="
