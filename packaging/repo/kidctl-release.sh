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
#   4. kidctl-repo-publish.sh      auf den Server (Ziel: KIDCTL_REPO_TARGET oder $REPO_HOME/publish-target),
#   5. gh release create v<Version> GitHub-Release mit Installer, SHA-256, Link zum Lauf und Kurztext.
# Kurztext: --notes DATEI (Markdown, Englisch), sonst packaging/repo/notes/v<Version>.md, sonst die ersten Sätze der
# Changelog-Einträge in spec.md, die die Version nennen. Gibt es das Release schon, wird Schritt 5 übersprungen.
# Ohne --apply: nur anzeigen (die Schritte 2–5 laufen als Dry-Run). Braucht die gh CLI (angemeldet).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
ROOT="$(cd "$HERE/../.." && pwd)"
GITHUB_REPO=AdrianNoss/kidCTL
RUN=""
NOTES=""
args=("$@")
for i in "${!args[@]}"; do
  a="${args[$i]}"
  [[ "$a" =~ ^[0-9]+$ ]] && RUN="$a"
  [[ "$a" == "--notes" ]] && NOTES="${args[$((i + 1))]:-}"
  [[ "$a" == --notes=* ]] && NOTES="${a#--notes=}"
done
[[ -z "$NOTES" || -f "$NOTES" ]] || die "Notizdatei $NOTES fehlt"
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

echo "==> GitHub-Release v$ver"
exe="KidCTL-Setup-$ver.exe"
exe_path="$REPO_DIR/windows/$exe"
if [[ -f "$exe_path" ]]; then
  digest="$(sha256sum "$exe_path" | cut -d' ' -f1)"
elif (( APPLY )); then
  die "$exe_path fehlt – Schritt „Windows-Installer“ fehlgeschlagen?"
else
  digest="<SHA-256 nach dem Holen>"
fi
[[ -z "$NOTES" && -f "$HERE/notes/v$ver.md" ]] && NOTES="$HERE/notes/v$ver.md"
if [[ -n "$NOTES" ]]; then
  highlights="$(cat "$NOTES")"
else
  # Erster Satz jedes Changelog-Eintrags, der die Version nennt (spec.md ist deutsch)
  highlights="$(awk -v v="$ver" '
    /^## Changelog/ { on = 1; next }
    on && /^- [0-9]{4}-/ { if (e != "") print e; e = $0; next }
    on && /^  / { sub(/^ +/, " "); e = e $0; next }
    END { if (e != "") print e }' "$ROOT/spec.md" \
    | command grep -F -- "$ver" \
    | sed -E 's/^- [0-9-]+: //; s/^(([^.]|\.[^ ])*\.).*/\1/; s/^/- /' || true)"
  [[ -n "$highlights" ]] && highlights="Changelog (German):"$'\n\n'"$highlights"
fi
blob="https://github.com/$GITHUB_REPO/blob/main"
body="$(cat <<EOT
KidCTL is self-hosted parental control for **Windows 10/11** and **Linux** (Arch, Debian, Ubuntu with KDE Plasma or GNOME on Wayland). A server you run yourself gives parents a web interface to manage screen time, game time, allowed apps and websites; an agent on each child's computer enforces the rules, also offline. No cloud, no telemetry.

### Downloads

- **Windows:** \`$exe\` below – run as administrator on the child's computer and enter your server address and the pairing code from the web interface (Devices → Pair). This build is **not code-signed yet**, so Windows SmartScreen asks for confirmation.
- **Linux:** packages from your server's package repository (\`pacman -S kidctl\` / \`apt install kidctl\`), see [installation guide]($blob/docs/installation.md).
- **Server:** Docker Compose, see [installation guide]($blob/docs/installation.md).

SHA-256 \`$exe\`: \`$digest\`
Built by GitHub Actions from this tag ([run](https://github.com/$GITHUB_REPO/actions/runs/$RUN)).

### Highlights of this version

${highlights:-–}

[Code signing policy]($blob/docs/code-signing.md) · [Privacy]($blob/docs/code-signing.md#privacy) · [Changelog (German)]($blob/spec.md#changelog)
EOT
)"
exists=0
gh release view "v$ver" -R "$GITHUB_REPO" > /dev/null 2>&1 && exists=1
if (( exists && APPLY )); then
  echo "GitHub-Release v$ver gibt es schon – übersprungen."
elif (( APPLY )); then
  printf '%s\n' "$body" | gh release create "v$ver" "$exe_path" -R "$GITHUB_REPO" --verify-tag \
    --title "KidCTL $ver" --notes-file -
else
  echo "[dry-run] gh release create v$ver $exe_path -R $GITHUB_REPO --verify-tag --title \"KidCTL $ver\" --notes-file - <<"
  printf '%s\n' "$body" | sed 's/^/    | /'
  (( exists )) && echo "[dry-run] Hinweis: GitHub-Release v$ver gibt es schon – mit --apply würde übersprungen."
fi
echo "==> Release $ver fertig."
