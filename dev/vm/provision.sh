#!/usr/bin/env bash
# Provisionierung der kidctl-Test-VM (läuft IN der VM als root, per cloud-init beim ersten Start).
# Ohne --apply nur Dry-Run. Geänderte Konfigurationsdateien werden vorher als .bak gesichert.
set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
MARKER=/var/lib/kidctl-vm/provisioned

run() {
  if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi
}

# Schreibt Datei-Inhalt von stdin nach $1, sichert eine vorhandene Datei als .bak.
put_file() {
  local path="$1" content
  content="$(cat)"
  if (( APPLY )); then
    [[ -e "$path" && ! -e "$path.bak" ]] && cp -a "$path" "$path.bak"
    mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" > "$path"
  else
    echo "[dry-run] schreibe $path:"; printf '%s\n' "$content" | sed 's/^/    /'
  fi
}

PACKAGES=(
  # Desktop: KDE Plasma (Wayland) + SDDM
  plasma-desktop sddm sddm-kcm kscreen plasma-nm plasma-pa breeze-gtk kde-gtk-config
  xdg-desktop-portal-kde konsole dolphin kate spectacle
  noto-fonts noto-fonts-emoji
  # Browser (später per Policy konfiguriert)
  firefox chromium
  # Laufzeit für Agent/Tray (offizielle Repos, kein pip)
  python python-pydantic python-websockets python-dbus-next pyside6
  # Systemnahe Komponenten für spätere Tests
  nftables
  # Werkzeuge
  git vim htop less
)

echo "== kidctl-VM-Provisionierung ($(date -Is)) =="

if [[ -f "$MARKER" ]]; then
  echo "Bereits provisioniert ($MARKER) – nichts zu tun."
  exit 0
fi

# Arch-Cloud-Image: Keyring wird beim ersten Start initialisiert
run systemctl start pacman-init.service || true
run pacman -Syu --noconfirm --needed "${PACKAGES[@]}"

# Deutsche Sprache/Tastatur
if (( APPLY )); then
  [[ -e /etc/locale.gen.bak ]] || cp -a /etc/locale.gen /etc/locale.gen.bak
  sed -i 's/^#\(de_DE.UTF-8 UTF-8\)/\1/; s/^#\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
else
  echo "[dry-run] /etc/locale.gen: de_DE.UTF-8 und en_US.UTF-8 aktivieren"
fi
run locale-gen
put_file /etc/locale.conf <<'CONF'
LANG=de_DE.UTF-8
CONF
put_file /etc/vconsole.conf <<'CONF'
KEYMAP=de-latin1
CONF
# X11-/Wayland-Tastatur (wird von KDE/SDDM übernommen)
put_file /etc/X11/xorg.conf.d/00-keyboard.conf <<'CONF'
Section "InputClass"
        Identifier "system-keyboard"
        MatchIsKeyboard "on"
        Option "XkbLayout" "de"
EndSection
CONF

# SDDM-Greeter selbst unter Wayland (kwin), Plasma-Wayland-Sitzung
put_file /etc/sddm.conf.d/10-wayland.conf <<'CONF'
[General]
DisplayServer=wayland
GreeterEnvironment=QT_WAYLAND_SHELL_INTEGRATION=layer-shell

[Wayland]
CompositorCommand=kwin_wayland --drm --no-lockscreen --no-global-shortcuts --locale1

[Theme]
Current=breeze
CONF

run systemctl enable sddm.service
run systemctl set-default graphical.target

run mkdir -p "$(dirname "$MARKER")"
run touch "$MARKER"
echo "== Provisionierung abgeschlossen – Neustart aktiviert SDDM =="
