#!/usr/bin/env bash
# Lokales Repo auf den Server kopieren (Docker-Stack, Bind-Mount ./data/repo).
#
#   KIDCTL_REPO_TARGET=user@server:/pfad/zum/stack/data/repo/ kidctl-repo-publish.sh [--apply]
#
# Ohne --apply zeigt rsync nur, was übertragen würde. Der Server liefert das Verzeichnis unter
# <BASE_URL>/repo/ aus.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

target="${KIDCTL_REPO_TARGET:-}"
[[ -n "$target" ]] || die "KIDCTL_REPO_TARGET setzen, z. B. user@server:/srv/kidctl/data/repo/"
[[ -f "$REPO_DIR/$REPO_NAME.db.tar.zst" ]] || die "kein Repo in $REPO_DIR – erst kidctl-repo-build.sh --apply"
opts=(-av --delete --links)
(( APPLY )) || opts+=(--dry-run)
rsync "${opts[@]}" "$REPO_DIR/" "$target"
(( APPLY )) || echo "[dry-run] mit --apply übertragen."
