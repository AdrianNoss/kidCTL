#!/usr/bin/env bash
# Namensauflösung über Port 53 erzwingen (für den kidctl-DNS-Filter).
#
# Arch nutzt standardmäßig nss-resolve: Programme fragen systemd-resolved dann über einen Socket
# statt über Port 53 – an der nftables-Umleitung vorbei. Dieses Skript entfernt „resolve“ aus der
# hosts:-Zeile; die Auflösung läuft weiter über systemd-resolved (127.0.0.53, Modul „dns“).
#
#   kidctl-nsswitch.sh status
#   kidctl-nsswitch.sh install [--apply]
#   kidctl-nsswitch.sh remove  [--apply]   ursprüngliche Zeile wiederherstellen
#
# Ohne --apply nur Dry-Run (Diff). Vorher Sicherung nach <datei>.kidctl.bak.
set -euo pipefail

FILE="${KIDCTL_NSSWITCH:-/etc/nsswitch.conf}"
MARK="# kidctl: ursprünglich: "
APPLY=0
CMD="${1:-}"
[[ "${2:-}" == "--apply" ]] && APPLY=1

die() { echo "Fehler: $*" >&2; exit 1; }
[[ -f "$FILE" ]] || die "$FILE nicht gefunden"

installed() { grep -q "^$MARK" "$FILE"; }

new_install() {
  awk -v mark="$MARK" '
    /^hosts:/ && !done {
      orig = $0
      line = $0
      gsub(/[[:space:]]+resolve([[:space:]]+\[[^]]*\])?/, "", line)
      if (line != orig) { print mark orig; print line; done = 1; next }
    }
    { print }' "$FILE"
}

new_remove() {
  awk -v mark="$MARK" '
    index($0, mark) == 1 { restore = substr($0, length(mark) + 1); skip = 1; next }
    skip && /^hosts:/ { print restore; skip = 0; next }
    { print }' "$FILE"
}

apply_content() {
  local new="$1"
  if diff -q "$FILE" <(printf '%s\n' "$new") >/dev/null; then echo "Keine Änderung nötig."; return; fi
  diff -u --label "$FILE (alt)" --label "$FILE (neu)" "$FILE" <(printf '%s\n' "$new") || true
  if (( APPLY )); then
    [[ $EUID -eq 0 || -n "${KIDCTL_NSSWITCH:-}" ]] || die "als root ausführen"
    [[ -e "$FILE.kidctl.bak" ]] || cp -a "$FILE" "$FILE.kidctl.bak"
    local tmp; tmp="$(mktemp "$FILE.XXXXXX")"
    printf '%s\n' "$new" > "$tmp"
    chmod --reference="$FILE" "$tmp"; chown --reference="$FILE" "$tmp" 2>/dev/null || true
    mv "$tmp" "$FILE"
    echo "Geschrieben. Sicherung: $FILE.kidctl.bak"
  else
    echo "[dry-run] mit --apply ausführen."
  fi
}

case "$CMD" in
  status) if installed; then echo "kidctl-Anpassung aktiv: $(grep '^hosts:' "$FILE")"; else echo "nicht angepasst: $(grep '^hosts:' "$FILE")"; fi ;;
  install) installed && { echo "Bereits angepasst."; exit 0; }; apply_content "$(new_install)" ;;
  remove) installed || { echo "Nichts zu tun."; exit 0; }; apply_content "$(new_remove)" ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
