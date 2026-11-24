#!/usr/bin/env bash
# Vorab-Version aus dem Branch dev für die Testinstanz (dev.kidctl.de) veröffentlichen – Gegenstück zu
# kidctl-release.sh, aber ohne Tag und ohne GitHub-Release.
#
#   kidctl-dev-release.sh [--apply] [LAUF-ID]
#
# Vorher: Push auf dev – der Workflow prerelease.yml baut mit Vorab-Version (z. B. 0.48.0dev312, devversion.py).
# Dieses Skript
#   1. nimmt den neuesten Lauf von prerelease.yml auf dev (oder LAUF-ID) und wartet, bis er fertig ist,
#   2. kidctl-repo-build.sh --dev --ci   Linux-Pakete holen, GPG-signieren (Prod-Schlüssel), Repo …/repo-dev,
#   3. build.py fetch/sign --dev         Windows-Installer (unsigniert, kein SignPath), latest.json signieren,
#   4. kidctl-repo-publish.sh --dev      auf die Testinstanz (KIDCTL_DEV_REPO_TARGET oder publish-target-dev).
# Das Prod-Repo (…/repo, publish-target) bleibt unberührt. Ohne --apply nur anzeigen. Braucht die gh CLI.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -- --dev "$@"
source "$HERE/common.sh"
ROOT="$(cd "$HERE/../.." && pwd)"
GITHUB_REPO=AdrianNoss/kidCTL
RUN=""
for a in "$@"; do [[ "$a" =~ ^[0-9]+$ ]] && RUN="$a"; done
apply=(); (( APPLY )) && apply=(--apply)

if [[ -z "$RUN" ]]; then
  RUN="$(gh run list -R "$GITHUB_REPO" -w prerelease.yml -b dev -L 1 --json databaseId -q '.[0].databaseId' || true)"
  [[ -n "$RUN" ]] || die "kein Lauf von prerelease.yml auf dev – dev gepusht oder Workflow von Hand starten"
fi
echo "==> Vorab-Version für die Testinstanz, Lauf $RUN: https://github.com/$GITHUB_REPO/actions/runs/$RUN"
if (( APPLY )); then
  gh run watch "$RUN" -R "$GITHUB_REPO" --exit-status --interval 30 > /dev/null || die "Lauf $RUN fehlgeschlagen"
else
  echo "[dry-run] gh run watch $RUN (Status: $(gh run view "$RUN" -R "$GITHUB_REPO" --json status,conclusion -q '.status + " " + .conclusion'))"
fi

echo "==> Linux-Pakete"
"$HERE/kidctl-repo-build.sh" --dev "${apply[@]}" --ci "$RUN"
echo "==> Windows-Installer"
python3 "$ROOT/packaging/windows/build.py" fetch --dev "${apply[@]}" --run "$RUN"
python3 "$ROOT/packaging/windows/build.py" sign --dev "${apply[@]}"
echo "==> Veröffentlichen (Testinstanz)"
"$HERE/kidctl-repo-publish.sh" --dev "${apply[@]}"
echo "==> Vorab-Version veröffentlicht – Testgeräte: Update-Knopf in der WebUI von dev.kidctl.de."
