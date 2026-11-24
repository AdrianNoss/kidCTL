# Testinstanz dev.kidctl.de

Neue Entwicklung landet zuerst auf dem Branch `dev` und wird auf `https://dev.kidctl.de` getestet. `main` (und
damit Release, Projektseite und `app.kidctl.de`) bekommt sie erst nach Adrians ausdrücklichem OK. So arbeiten die
Familien auf Prod ungestört.

## Ablauf

1. Claude entwickelt in Worktrees, führt Branches auf `dev` zusammen und pusht `dev` nach grünen lokalen Tests.
   GitHub Actions testet jeden Push auf `dev` (`tests.yml`); Release und Projektseite laufen nur auf `main`.
2. Adrian aktualisiert die Testinstanz und testet dort (WebUI, Testgeräte).
3. Nach Adrians OK: `dev` per Fast-Forward nach `main`, Version/News/Release Notes wie bisher, Release und
   Prod-Update durch Adrian.
4. **Ausnahme Sicherheitsfix:** direkt auf `main` (Patch-Release), danach `main` nach `dev` mergen.

Versionsnummer, News und Release Notes werden erst beim Übergang nach `main` gesetzt.

## Einrichtung auf srv02 (einmalig, Adrian)

1. DNS: `dev.kidctl.de` auf srv02 (wie `app.kidctl.de`).
2. Eigener Ordner, Branch `dev`:

   ```bash
   git clone https://github.com/AdrianNoss/kidCTL.git /opt/kidCTL-dev
   cd /opt/kidCTL-dev && git switch dev
   cp .env.example .env
   ```

3. `.env` – **eigene** Schlüssel, nie die von Prod:
   - `SECRET_KEY`, `KIDCTL_MASTER_KEY` (`docker compose run --rm kidctl python -m app.cli master-key`),
     `POSTGRES_PASSWORD` neu erzeugen.
   - `BASE_URL=https://dev.kidctl.de`, `KIDCTL_HOST=dev.kidctl.de`, `KIDCTL_NAME=kidctl-dev`.
   - Betriebsart wie Prod (`KIDCTL_MODE`, `REGISTRATION`). Abo-Webhook leer lassen oder nur Testmodus des
     Zahlungsanbieters.
   - SMTP optional; auf dev nur Testkonten mit eigenen Adressen anlegen.
4. Start und erster Eltern-Account:

   ```bash
   docker compose up -d --build
   docker compose run --rm kidctl python -m app.cli create-parent
   ```

5. Testdaten: nur eigene Testfamilien und Testgeräte, keine Kopie der Prod-Datenbank.

## Aktualisieren

```bash
cd /opt/kidCTL-dev && git pull && docker compose up -d --build
```

## Testgeräte

Test-VMs (`dev/vm/`) und Test-PC mit `https://dev.kidctl.de` koppeln (`kidctl enroll https://dev.kidctl.de <Code>`
bzw. Windows-Installer von der Download-Seite der Testinstanz). Linux-Testgeräte bekommen das Paket-Repo der
Testinstanz – gleicher Schlüssel wie Prod, also gleicher Fingerabdruck:

```bash
sudo bash kidctl-repo-client.sh https://dev.kidctl.de/repo <FINGERABDRUCK> --apply
```

Danach aktualisieren sie sich per Update-Knopf in der WebUI von dev.kidctl.de wie Familien auf Prod (Windows prüft
`latest.json` selbst alle 6 Stunden).

> [!NOTE]
> Windows-Agenten bis 0.47.0 kennen keine Vorab-Versionen und lehnen ein `latest.json` mit `devN` ab. Ein
> Windows-Testgerät, auf dem noch ein Release läuft, einmal von Hand mit dem Installer der Testinstanz aktualisieren;
> danach aktualisiert es sich selbst. Für schnelle Zwischenstände ohne Bau geht weiterhin `makepkg` in der VM bzw.
`dev/vm/winsync.sh`.

## Paketkanal der Testinstanz (Vorab-Versionen)

Entschieden mit Adrian (2026-11-24):

| Thema | Regel |
|---|---|
| Version | Nächste Minor-Version nach `__version__` + `dev` + Anzahl der Commits (`git rev-list --count HEAD`), z. B. `0.48.0dev312`. Debian: `0.48.0~dev312`, Windows-Dateiversion `0.47.65535.312`. Jede Vorab-Version ist kleiner als das Release `0.48.0` und größer als `0.47.x` – in pacman, dpkg, Server und Windows-Agent (`common/kidctl_common/version.py`). |
| Bau | Workflow `prerelease.yml` bei Push auf `dev` (nur bei Änderungen an `agent/`, `tray/`, `common/`, `packaging/`) oder von Hand. Die Version entsteht nur im Bau (`packaging/devversion.py set`), sie wird nie committet. Kein Tag, kein GitHub-Release. |
| Signatur | Gleicher GPG-Repo-Schlüssel und Windows-Update-Schlüssel wie Prod, lokal bei Adrian. Kein SignPath – der Installer der Testinstanz ist unsigniert (SmartScreen fragt). |
| Ablage | Lokal `~/.local/share/kidctl-repo/repo-dev` (nie mit `repo` gemischt), Ziel `KIDCTL_DEV_REPO_TARGET` oder Datei `publish-target-dev`. Ohne dev-Ziel bricht die Veröffentlichung ab; das Prod-Ziel wird abgelehnt. |

Einrichtung (einmalig, Adrian): Ziel der Testinstanz eintragen.

```bash
echo 'user@srv02:/opt/kidCTL-dev/data/repo/' > ~/.local/share/kidctl-repo/publish-target-dev
```

Vorab-Version veröffentlichen (nach einem Push auf `dev`, Adrian):

```bash
packaging/repo/kidctl-dev-release.sh            # Dry-Run: zeigt Lauf und Schritte
packaging/repo/kidctl-dev-release.sh --apply    # wartet auf den Lauf, holt, signiert, veröffentlicht
```

Das Skript nimmt den neuesten Lauf von `prerelease.yml` auf `dev` (oder eine Lauf-ID als Argument), holt Linux-Pakete
und Installer, signiert lokal und überträgt `repo-dev` per rsync auf die Testinstanz. Prod-Repo, Tags und der
Release-Ablauf auf `main` bleiben unverändert.

Nach einem Release (z. B. `0.48.0` auf `main`, danach `main` → `dev`) baut `dev` automatisch `0.49.0devN`. Das Release
selbst kommt nicht in das Repo der Testinstanz; Testgeräte springen direkt auf die nächste Vorab-Version. Soll ein
Testgerät zurück auf Prod, dort neu koppeln und das Paket-Repo auf `https://app.kidctl.de/repo` umstellen; die
höhere Vorab-Version bleibt installiert, bis das Release sie überholt.
