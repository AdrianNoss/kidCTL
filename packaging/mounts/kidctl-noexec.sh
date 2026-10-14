#!/usr/bin/env bash
# noexec für die von allen Usern beschreibbaren Orte: /tmp, /var/tmp, /dev/shm.
# (Kinder-Homes hängt der kidctl-Agent selbst je Kind mit noexec ein.)
#
#   kidctl-noexec.sh status
#   kidctl-noexec.sh apply  [--apply]   noexec setzen (idempotent)
#   kidctl-noexec.sh revert [--apply]   wieder ausführbar machen
#
# Ohne --apply nur Dry-Run. Ändert nur Mount-Optionen (remount), keine Dateien.
set -euo pipefail

DIRS=(/tmp /var/tmp /dev/shm)
APPLY=0
CMD="${1:-}"
[[ "${2:-}" == "--apply" ]] && APPLY=1

run() { if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi; }
is_mountpoint() { findmnt -n --mountpoint "$1" >/dev/null 2>&1; }
has_noexec() { findmnt -n -o OPTIONS --mountpoint "$1" 2>/dev/null | tr , '\n' | grep -qx noexec; }

case "$CMD" in
  status)
    for d in "${DIRS[@]}"; do
      if ! is_mountpoint "$d"; then echo "$d: kein eigener Mount (ausführbar)"
      elif has_noexec "$d"; then echo "$d: noexec"
      else echo "$d: ausführbar"; fi
    done ;;
  apply)
    (( APPLY )) && [[ $EUID -ne 0 ]] && { echo "als root ausführen" >&2; exit 1; }
    for d in "${DIRS[@]}"; do
      [[ -d "$d" ]] || continue
      if ! is_mountpoint "$d"; then
        # z. B. /var/tmp auf dem Root-Dateisystem: Bind-Mount auf sich selbst
        run mount --bind "$d" "$d"
        run mount -o remount,bind,noexec,nosuid,nodev "$d"
      elif ! has_noexec "$d"; then
        run mount -o remount,noexec "$d"
      fi
    done ;;
  revert)
    (( APPLY )) && [[ $EUID -ne 0 ]] && { echo "als root ausführen" >&2; exit 1; }
    for d in "${DIRS[@]}"; do
      is_mountpoint "$d" || continue
      if [[ "$d" == /var/tmp ]] && [[ "$(findmnt -n -o SOURCE --mountpoint "$d")" == *"[/var/tmp]" ]]; then
        run umount "$d"    # unser Bind-Mount
      elif has_noexec "$d"; then
        run mount -o remount,exec "$d"
      fi
    done ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
