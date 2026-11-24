#!/usr/bin/env bash
# Pakete bauen (kidctl, kidctl-plasma, kidctl-gnome), signieren und ins lokale Repo aufnehmen (Datenbank
# ebenfalls signiert). Zusätzlich .deb-Pakete für Debian/Ubuntu und ein signiertes APT-Repo unter repo/deb.
#
#   kidctl-repo-build.sh [--apply] [--ci [LAUF-ID]]
#
# --ci: nicht lokal bauen, sondern die Pakete aus GitHub Actions (Workflow release.yml, Artefakt kidctl-linux)
# holen – Lauf zum Tag v<Version>, sonst der neueste erfolgreiche – und hier signieren. Braucht die gh CLI.
#
# --dev (nur mit --ci): Paketkanal der Testinstanz – Pakete aus prerelease.yml (neuester erfolgreicher Lauf auf dev),
# Vorab-Version aus den Paketen (z. B. 0.48.0dev312), Repo unter …/repo-dev, gleicher Signierschlüssel.
#
# Ergebnis: ${KIDCTL_REPO_HOME:-~/.local/share/kidctl-repo}/repo mit kidctl.db (signiert),
# Paketen + .sig, dem öffentlichen Schlüssel kidctl-repo.asc und kidctl-repo-client.sh. Ältere Paketversionen werden
# entfernt (repo-add -R). Danach: kidctl-repo-publish.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
GITHUB_REPO=AdrianNoss/kidCTL
WORKFLOW=release.yml; [[ "$CHANNEL" == dev ]] && WORKFLOW=prerelease.yml
CI=0; CI_RUN=""
args=("$@")
for i in "${!args[@]}"; do
  if [[ "${args[$i]}" == "--ci" ]]; then
    CI=1; next="${args[$((i + 1))]:-}"; [[ "$next" =~ ^[0-9]+$ ]] && CI_RUN="$next"
  fi
done
[[ "$CHANNEL" != dev ]] || (( CI )) || die "--dev nur mit --ci (Vorab-Version entsteht im Bau, devversion.py)"

fpr="$(fingerprint)"; [[ -n "$fpr" ]] || die "kein Signierschlüssel – erst kidctl-repo-key.sh create --apply"
[[ -f "$REPO_HOME/$PUBKEY_NAME" ]] || die "öffentlicher Schlüssel fehlt – kidctl-repo-key.sh export"
ARCHDIR="$HERE/../arch"
ver="$(sed -n 's/^pkgver=\([^ ]*\).*/\1/p' "$ARCHDIR/PKGBUILD")"
rel="$(sed -n 's/^pkgrel=\(.*\)/\1/p' "$ARCHDIR/PKGBUILD")"
names="$(sed -n 's/^pkgname=(\(.*\))/\1/p' "$ARCHDIR/PKGBUILD")"
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT

run mkdir -p "$REPO_DIR"
if (( CI )); then
  if [[ -z "$CI_RUN" ]]; then
    newest() { gh run list -R "$GITHUB_REPO" -w "$WORKFLOW" -s success -L 1 "$@" --json databaseId -q '.[0].databaseId' 2>/dev/null || true; }
    if [[ "$CHANNEL" == dev ]]; then
      CI_RUN="$(newest -b dev)"
    else
      tag="$(gh api "repos/$GITHUB_REPO/commits/v$ver" --jq .sha 2>/dev/null || true)"
      [[ -n "$tag" ]] && CI_RUN="$(newest -c "$tag")"; [[ -n "$CI_RUN" ]] || CI_RUN="$(newest)"
    fi
    [[ -n "$CI_RUN" ]] || die "kein erfolgreicher Lauf von $WORKFLOW"
  fi
  run gh run download "$CI_RUN" -R "$GITHUB_REPO" -n kidctl-linux -D "$build/ci"
  if [[ "$CHANNEL" == dev ]]; then
    # Vorab-Version steht nur im Bau – aus dem Paketnamen lesen (kidctl-0.48.0dev312-1-any.pkg.tar.zst)
    if (( APPLY )); then
      f="$(cd "$build/ci/arch" && ls kidctl-[0-9]*-any.pkg.tar.zst 2>/dev/null | head -n1 || true)"
      [[ "$f" =~ ^kidctl-([0-9.]+dev[0-9]+)-([0-9]+)-any\.pkg\.tar\.zst$ ]] \
        || die "Lauf $CI_RUN enthält kein kidctl-Paket mit Vorab-Version (${f:-nichts})"
      ver="${BASH_REMATCH[1]}"; rel="${BASH_REMATCH[2]}"
    else
      ver="<Vorab-Version>"
    fi
  fi
  echo "Hole $names $ver-$rel aus GitHub Actions (Lauf $CI_RUN), signiert mit $fpr"
  pkgs=()
  for n in $names; do pkgs+=("$REPO_DIR/$n-$ver-$rel-any.pkg.tar.zst"); done
  for pkg in "${pkgs[@]}"; do
    src="$build/ci/arch/$(basename "$pkg")"
    if (( APPLY )); then [[ -f "$src" ]] || die "$(basename "$pkg") fehlt in Lauf $CI_RUN – passt die Version?"; fi
    run install -m644 "$src" "$pkg"
    run gpg --batch --yes --detach-sign --no-armor --local-user "$fpr" -o "$pkg.sig" "$pkg"
  done
else
  pkgs=()
  for n in $names; do pkgs+=("$REPO_DIR/$n-$ver-$rel-any.pkg.tar.zst"); done
  echo "Baue $names $ver-$rel, signiert mit $fpr"
  # --nodeps: reines Python (arch=any) – Laufzeit-Abhängigkeiten braucht der Bau-Rechner nicht
  (cd "$ARCHDIR" && run env BUILDDIR="$build" PKGDEST="$REPO_DIR" SRCDEST="$build" LOGDEST="$build" \
     makepkg --force --cleanbuild --nodeps --sign --key "$fpr")
fi
run repo-add --remove --sign --key "$fpr" "$REPO_DIR/$REPO_NAME.db.tar.zst" "${pkgs[@]}"
run cp "$REPO_HOME/$PUBKEY_NAME" "$REPO_DIR/$PUBKEY_NAME"
# Debian/Ubuntu: .deb (ohne dpkg gebaut) und APT-Repo, signiert mit demselben Schlüssel
if (( CI )); then debs="$build/ci/deb"; else debs="$build/deb"; run python3 "$HERE/../debian/build-deb.py" "$debs"; fi
run python3 "$HERE/../debian/apt-repo.py" "$debs" "$REPO_DIR/deb" --key "$fpr"
# Einrichtungsskript für Kinderrechner mit ausliefern (…/repo/kidctl-repo-client.sh)
run install -m644 "$HERE/kidctl-repo-client.sh" "$REPO_DIR/kidctl-repo-client.sh"
(( APPLY )) && { echo "Repo:"; ls -1 "$REPO_DIR"; }
true
