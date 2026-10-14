#!/usr/bin/env bash
# Kinderrechner: kidctl-Repository einrichten (als root).
#
#   kidctl-repo-client.sh <REPO-URL> <FINGERABDRUCK> [--apply]
#   z. B. kidctl-repo-client.sh https://kids.nossco.de/repo 0123…ABCD --apply
#
# Lädt den öffentlichen Schlüssel, prüft ihn gegen den angegebenen Fingerabdruck (aus
# kidctl-repo-key.sh), vertraut ihm lokal (pacman-key --lsign-key) und trägt [kidctl] in
# /etc/pacman.conf ein (Sicherung pacman.conf.kidctl.bak, idempotent). Danach: pacman -Syu kidctl.
set -euo pipefail

URL="${1:-}"; FPR="${2:-}"
APPLY=0; [[ "${3:-}" == "--apply" ]] && APPLY=1
CONF="${KIDCTL_PACMAN_CONF:-/etc/pacman.conf}"
die() { echo "Fehler: $*" >&2; exit 1; }
run() { if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi; }
[[ -n "$URL" && -n "$FPR" ]] || { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
URL="${URL%/}"; FPR="${FPR// /}"; FPR="${FPR^^}"
(( APPLY )) && [[ $EUID -ne 0 ]] && die "als root ausführen"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$URL/kidctl-repo.asc" -o "$tmp/key.asc" || die "Schlüssel nicht abrufbar: $URL/kidctl-repo.asc"
got="$(gpg --homedir "$tmp" --batch --with-colons --import-options show-only --import "$tmp/key.asc" 2>/dev/null \
       | awk -F: '/^fpr:/ {print $10; exit}')"
[[ "$got" == "$FPR" ]] || die "Fingerabdruck passt nicht! erwartet $FPR, erhalten ${got:-nichts}"
echo "Schlüssel geprüft: $got"

run pacman-key --add "$tmp/key.asc"
run pacman-key --lsign-key "$FPR"
if grep -q '^\[kidctl\]' "$CONF"; then
  echo "[kidctl] bereits in $CONF"
else
  block=$'\n# kidctl – Parental Control (verwaltet von kidctl-repo-client.sh)\n[kidctl]\nSigLevel = Required DatabaseRequired\nServer = '"$URL"
  if (( APPLY )); then
    [[ -e "$CONF.kidctl.bak" ]] || cp -a "$CONF" "$CONF.kidctl.bak"
    printf '%s\n' "$block" >> "$CONF"
    echo "[kidctl] in $CONF eingetragen (Sicherung $CONF.kidctl.bak)"
  else
    echo "[dry-run] an $CONF anhängen:"; printf '%s\n' "$block" | sed 's/^/    /'
  fi
fi
run pacman -Sy
