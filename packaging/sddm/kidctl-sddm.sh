#!/usr/bin/env bash
# kidctl-SDDM-Theme installieren/entfernen (Hinweis der Login-Sperre im Anmeldebildschirm).
#
#   kidctl-sddm.sh status
#   kidctl-sddm.sh install [--apply]
#   kidctl-sddm.sh remove  [--apply]
#
# Ohne --apply nur Dry-Run. Wirkt ab dem nächsten Start des Anmeldebildschirms.
# Voraussetzung: Breeze-SDDM-Theme (plasma-desktop) unter /usr/share/sddm/themes/breeze.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME_DIR="${KIDCTL_SDDM_THEME_DIR:-/usr/share/sddm/themes/kidctl}"
CONF="${KIDCTL_SDDM_CONF:-/etc/sddm.conf.d/zz-kidctl-theme.conf}"
BREEZE=/usr/share/sddm/themes/breeze

APPLY=0
CMD="${1:-}"
[[ "${2:-}" == "--apply" ]] && APPLY=1

run() { if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi; }
die() { echo "Fehler: $*" >&2; exit 1; }

case "$CMD" in
  status)
    [[ -f "$THEME_DIR/Main.qml" ]] && echo "Theme installiert: $THEME_DIR" || echo "Theme nicht installiert"
    [[ -f "$CONF" ]] && echo "Aktiviert über $CONF" || echo "nicht aktiviert" ;;
  install)
    [[ -f "$BREEZE/Main.qml" ]] || die "Breeze-SDDM-Theme fehlt ($BREEZE)"
    (( APPLY )) && [[ $EUID -ne 0 && -z "${KIDCTL_SDDM_THEME_DIR:-}" ]] && die "als root ausführen"
    if [[ -e "$CONF" ]] && ! cmp -s "$CONF" "$HERE/zz-kidctl-theme.conf"; then
      run cp -a "$CONF" "$CONF.kidctl.bak"
    fi
    run install -d -m 0755 "$THEME_DIR"
    run install -m 0644 "$HERE/theme/Main.qml" "$HERE/theme/metadata.desktop" "$THEME_DIR/"
    run ln -sfn "$BREEZE/theme.conf" "$THEME_DIR/theme.conf"
    run install -D -m 0644 "$HERE/zz-kidctl-theme.conf" "$CONF"
    (( APPLY )) && echo "Installiert. Wirkt ab dem nächsten Anmeldebildschirm." ;;
  remove)
    (( APPLY )) && [[ $EUID -ne 0 && -z "${KIDCTL_SDDM_THEME_DIR:-}" ]] && die "als root ausführen"
    run rm -f "$CONF"
    run rm -rf "$THEME_DIR" ;;
  *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
