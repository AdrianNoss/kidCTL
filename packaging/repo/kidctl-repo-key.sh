#!/usr/bin/env bash
# Signierschlüssel für das kidctl-Pacman-Repository anlegen (eigener GPG-Ordner, getrennt vom
# persönlichen Schlüsselbund).
#
#   kidctl-repo-key.sh status
#   kidctl-repo-key.sh create [--apply]   Ed25519, 3 Jahre gültig, Passphrase über pinentry
#   kidctl-repo-key.sh export             öffentlichen Schlüssel nach $REPO_HOME/kidctl-repo.asc
#
# Ordner: ${KIDCTL_REPO_HOME:-~/.local/share/kidctl-repo}/gnupg. Den privaten Schlüssel sichern
# (z. B. „gpg --export-secret-keys“ in einen Passwortmanager) – ohne ihn lassen sich keine Updates
# mehr signieren, die Kinderrechner akzeptieren.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

case "${1:-}" in
  status)
    fpr="$(fingerprint)"
    if [[ -n "$fpr" ]]; then gpg --list-secret-keys "$fpr"; else echo "Noch kein Schlüssel in $GNUPGHOME"; fi ;;
  create)
    [[ -z "$(fingerprint)" ]] || die "In $GNUPGHOME existiert bereits ein Schlüssel ($(fingerprint))."
    run mkdir -p -m 0700 "$GNUPGHOME"
    if [[ "${KIDCTL_GPG_NOPASS:-}" == 1 ]]; then   # nur für automatische Tests
      run gpg --batch --pinentry-mode loopback --passphrase "" --quick-gen-key "$KEY_UID" ed25519 sign 3y
    else
      run gpg --quick-gen-key "$KEY_UID" ed25519 sign 3y
    fi
    if (( APPLY )); then
      "$0" export
      echo "Fingerabdruck (für kidctl-repo-client.sh): $(fingerprint)"
    fi ;;
  export)
    fpr="$(fingerprint)"; [[ -n "$fpr" ]] || die "kein Schlüssel – erst „create --apply“"
    gpg --armor --export "$fpr" > "$REPO_HOME/$PUBKEY_NAME"
    echo "Öffentlicher Schlüssel: $REPO_HOME/$PUBKEY_NAME" ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
