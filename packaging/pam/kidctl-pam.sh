#!/usr/bin/env bash
# PAM-Login-Sperre für kidctl in /etc/pam.d/system-login ein-/austragen.
#
#   kidctl-pam.sh status
#   kidctl-pam.sh install [--apply]   Block (neu) setzen – idempotent
#   kidctl-pam.sh remove  [--apply]
#
# Ohne --apply nur Dry-Run (zeigt das Ergebnis als Diff). Vor jeder Änderung Sicherung nach
# <datei>.kidctl.bak (erste Originalversion bleibt erhalten).
#
# Der Block greift für alles, was system-login im Account-Teil einbindet: SDDM (inkl. Autologin),
# TTY-Login, SSH. Fehlt /run/kidctl/locked-users, ist die Anmeldung erlaubt (onerr=succeed).
set -euo pipefail

PAM_FILE="${KIDCTL_PAM_FILE:-/etc/pam.d/system-login}"
BEGIN="# kidctl begin – Login-Sperre Bildschirmzeit (verwaltet von kidctl-pam.sh)"
END="# kidctl end"
BLOCK="$BEGIN
account    [success=2 default=ignore]  pam_listfile.so onerr=succeed item=user sense=deny file=/run/kidctl/locked-users
account    optional                    pam_echo.so file=/run/kidctl/locked-message
account    requisite                   pam_deny.so
$END"

APPLY=0
CMD="${1:-}"
[[ "${2:-}" == "--apply" ]] && APPLY=1

die() { echo "Fehler: $*" >&2; exit 1; }

strip_block() {  # Datei ohne kidctl-Block
  awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$PAM_FILE"
}

with_block() {   # Block direkt nach der letzten account-Zeile einfügen
  strip_block | awk -v block="$BLOCK" '
    { lines[NR]=$0 }
    /^[[:space:]]*-?account[[:space:]]/ { last=NR }
    END {
      if (!last) { print "keine account-Zeile gefunden" > "/dev/stderr"; exit 1 }
      for (i=1; i<=NR; i++) { print lines[i]; if (i==last) print block }
    }'
}

apply_content() {
  local new="$1"
  if diff -q <(cat "$PAM_FILE") <(printf '%s\n' "$new") >/dev/null; then
    echo "Keine Änderung nötig."
    return
  fi
  diff -u --label "$PAM_FILE (alt)" --label "$PAM_FILE (neu)" "$PAM_FILE" <(printf '%s\n' "$new") || true
  if (( APPLY )); then
    [[ -e "$PAM_FILE.kidctl.bak" ]] || cp -a "$PAM_FILE" "$PAM_FILE.kidctl.bak"
    local tmp; tmp="$(mktemp "$PAM_FILE.XXXXXX")"
    printf '%s\n' "$new" > "$tmp"
    chmod --reference="$PAM_FILE" "$tmp"
    chown --reference="$PAM_FILE" "$tmp"
    mv "$tmp" "$PAM_FILE"
    echo "Geschrieben. Sicherung: $PAM_FILE.kidctl.bak"
  else
    echo "[dry-run] mit --apply ausführen."
  fi
}

[[ -f "$PAM_FILE" ]] || die "$PAM_FILE nicht gefunden"
case "$CMD" in
  status)
    if grep -qxF "$BEGIN" "$PAM_FILE"; then echo "kidctl-Block vorhanden in $PAM_FILE"; else echo "kein kidctl-Block in $PAM_FILE"; fi ;;
  install)
    (( APPLY )) && [[ $EUID -ne 0 && -z "${KIDCTL_PAM_FILE:-}" ]] && die "als root ausführen"
    apply_content "$(with_block)" ;;
  remove)
    (( APPLY )) && [[ $EUID -ne 0 && -z "${KIDCTL_PAM_FILE:-}" ]] && die "als root ausführen"
    apply_content "$(strip_block)" ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
