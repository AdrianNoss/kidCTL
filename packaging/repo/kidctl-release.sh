#!/usr/bin/env bash
# Release abschließen: auf den Bau in GitHub Actions warten, Pakete holen, lokal signieren, veröffentlichen.
#
#   kidctl-release.sh [--apply] [LAUF-ID]
#
# Vorher: Version in agent/kidctl_agent/__init__.py und packaging/arch/PKGBUILD erhöhen, committen, auf main
# pushen – der Workflow release.yml legt dann das Tag v<Version> an und baut. Dieses Skript
#   1. sucht den Lauf zum Commit des Tags (wartet, bis es ihn gibt und er fertig ist),
#   2. kidctl-repo-build.sh --ci   Linux-Pakete holen, GPG-signieren, pacman-/APT-Repo aktualisieren,
#   3. build.py fetch + sign       Windows-Installer holen, latest.json mit dem Update-Schlüssel signieren,
#   4. kidctl-repo-publish.sh      auf den Server (Ziel: KIDCTL_REPO_TARGET oder $REPO_HOME/publish-target).
# Ohne --apply: nur anzeigen (die Schritte 2–4 laufen als Dry-Run). Braucht die gh CLI (angemeldet).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
ROOT="$(cd "$HERE/../.." && pwd)"
GITHUB_REPO=AdrianNoss/kidCTL
RUN=""
for a in "$@"; do [[ "$a" =~ ^[0-9]+$ ]] && RUN="$a"; done
apply=(); (( APPLY )) && apply=(--apply)

ver="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/agent/kidctl_agent/__init__.py")"
[[ -n "$ver" ]] || die "Version in agent/kidctl_agent/__init__.py nicht gefunden"
echo "==> Release $ver"

if [[ -z "$RUN" ]]; then
  # Tag und Lauf erscheinen erst nach dem Push – bis zu 10 Minuten warten
  for i in $(seq 1 40); do
    sha="$(gh api "repos/$GITHUB_REPO/commits/v$ver" --jq .sha 2>/dev/null || true)"
    if [[ -n "$sha" ]]; then
      RUN="$(gh run list -R "$GITHUB_REPO" -w release.yml -c "$sha" -L 1 --json databaseId -q '.[0].databaseId' || true)"
      [[ -n "$RUN" ]] && break
    fi
    if (( ! APPLY )); then
      [[ -n "$sha" ]] && die "Tag v$ver vorhanden, aber kein Lauf von release.yml zu ${sha:0:7} – LAUF-ID angeben?"
      die "Tag v$ver fehlt – Version erhöht und auf main gepusht?"
    fi
    (( i == 1 )) && echo "Warte auf Tag v$ver und den Lauf in GitHub Actions …"
    sleep 15
  done
  [[ -n "$RUN" ]] || die "nach 10 Minuten kein Lauf zu v$ver – Version erhöht und auf main gepusht?"
fi
echo "==> Lauf $RUN: https://github.com/$GITHUB_REPO/actions/runs/$RUN"
if (( APPLY )); then
  gh run watch "$RUN" -R "$GITHUB_REPO" --exit-status --interval 30 > /dev/null || die "Lauf $RUN fehlgeschlagen"
else
  echo "[dry-run] gh run watch $RUN (Status: $(gh run view "$RUN" -R "$GITHUB_REPO" --json status,conclusion -q '.status + " " + .conclusion'))"
fi

echo "==> Linux-Pakete"
"$HERE/kidctl-repo-build.sh" "${apply[@]}" --ci "$RUN"
echo "==> Windows-Installer"
python3 "$ROOT/packaging/windows/build.py" fetch "${apply[@]}" --run "$RUN"
python3 "$ROOT/packaging/windows/build.py" sign "${apply[@]}"
echo "==> Veröffentlichen"
"$HERE/kidctl-repo-publish.sh" "${apply[@]}"
echo "==> Release $ver fertig."
