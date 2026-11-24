#!/usr/bin/env bash
# Lokales Repo auf den Server kopieren (Docker-Stack, Bind-Mount ./data/repo).
#
#   KIDCTL_REPO_TARGET=user@server:/pfad/zum/stack/data/repo/ kidctl-repo-publish.sh [--apply]
#   KIDCTL_DEV_REPO_TARGET=user@server:/opt/kidCTL-dev/data/repo/ kidctl-repo-publish.sh --dev [--apply]
#
# Statt der Variable geht auch eine Zeile in ${KIDCTL_REPO_HOME:-~/.local/share/kidctl-repo}/publish-target
# (--dev: publish-target-dev). --dev überträgt repo-dev und nimmt nie das Prod-Ziel.
#
# Ohne --apply zeigt rsync nur, was übertragen würde. Der Server liefert das Verzeichnis unter
# <BASE_URL>/repo/ aus.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

read_target() { [[ -f "$REPO_HOME/$1" ]] && head -n1 "$REPO_HOME/$1" || true; }
prod="${KIDCTL_REPO_TARGET:-$(read_target publish-target)}"
if [[ "$CHANNEL" == dev ]]; then
  target="${KIDCTL_DEV_REPO_TARGET:-$(read_target publish-target-dev)}"
  [[ -n "$target" ]] || die "KIDCTL_DEV_REPO_TARGET setzen (oder $REPO_HOME/publish-target-dev), z. B. user@srv02:/opt/kidCTL-dev/data/repo/"
  [[ "${target%/}" != "${prod%/}" ]] || die "Ziel der Testinstanz ist das Prod-Ziel ($target) – abgebrochen"
else
  target="$prod"
  [[ -n "$target" ]] || die "KIDCTL_REPO_TARGET setzen (oder $REPO_HOME/publish-target), z. B. user@server:/srv/kidctl/data/repo/"
fi
[[ -f "$REPO_DIR/$REPO_NAME.db.tar.zst" ]] || die "kein Repo in $REPO_DIR – erst kidctl-repo-build.sh --apply"
opts=(-av --delete --links)
(( APPLY )) || opts+=(--dry-run)
rsync "${opts[@]}" "$REPO_DIR/" "$target"
(( APPLY )) || echo "[dry-run] mit --apply übertragen."
