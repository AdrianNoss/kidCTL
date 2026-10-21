#!/usr/bin/env bash
# Pakete bauen (kidctl, kidctl-plasma, kidctl-gnome), signieren und ins lokale Repo aufnehmen (Datenbank
# ebenfalls signiert).
#
#   kidctl-repo-build.sh [--apply]
#
# Ergebnis: ${KIDCTL_REPO_HOME:-~/.local/share/kidctl-repo}/repo mit kidctl.db (signiert),
# Paketen + .sig, dem öffentlichen Schlüssel kidctl-repo.asc und kidctl-repo-client.sh. Ältere Paketversionen werden
# entfernt (repo-add -R). Danach: kidctl-repo-publish.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"

fpr="$(fingerprint)"; [[ -n "$fpr" ]] || die "kein Signierschlüssel – erst kidctl-repo-key.sh create --apply"
[[ -f "$REPO_HOME/$PUBKEY_NAME" ]] || die "öffentlicher Schlüssel fehlt – kidctl-repo-key.sh export"
ARCHDIR="$HERE/../arch"
ver="$(sed -n 's/^pkgver=\([^ ]*\).*/\1/p' "$ARCHDIR/PKGBUILD")"
rel="$(sed -n 's/^pkgrel=\(.*\)/\1/p' "$ARCHDIR/PKGBUILD")"
names="$(sed -n 's/^pkgname=(\(.*\))/\1/p' "$ARCHDIR/PKGBUILD")"
pkgs=()
for n in $names; do pkgs+=("$REPO_DIR/$n-$ver-$rel-any.pkg.tar.zst"); done
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT

echo "Baue $names $ver-$rel, signiert mit $fpr"
run mkdir -p "$REPO_DIR"
# --nodeps: reines Python (arch=any) – Laufzeit-Abhängigkeiten braucht der Bau-Rechner nicht
(cd "$ARCHDIR" && run env BUILDDIR="$build" PKGDEST="$REPO_DIR" SRCDEST="$build" LOGDEST="$build" \
   makepkg --force --cleanbuild --nodeps --sign --key "$fpr")
run repo-add --remove --sign --key "$fpr" "$REPO_DIR/$REPO_NAME.db.tar.zst" "${pkgs[@]}"
run cp "$REPO_HOME/$PUBKEY_NAME" "$REPO_DIR/$PUBKEY_NAME"
# Einrichtungsskript für Kinderrechner mit ausliefern (…/repo/kidctl-repo-client.sh)
run install -m644 "$HERE/kidctl-repo-client.sh" "$REPO_DIR/kidctl-repo-client.sh"
(( APPLY )) && { echo "Repo:"; ls -1 "$REPO_DIR"; }
true
