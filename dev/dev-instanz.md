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
   cd /opt/kidCTL-dev && git checkout dev
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
bzw. Windows-Installer). Bis es einen eigenen Paketkanal gibt, bekommen sie neuen Agent-Code per `makepkg` in der
VM bzw. `dev/vm/winsync.sh`.

## Offen: eigener Paketkanal für dev

Entschieden (2026-11-24): dev.kidctl.de liefert ein eigenes Paket-Repo mit Vorab-Versionen, Testgeräte nutzen den
Update-Knopf wie Familien. Noch umzusetzen: Vorab-Versionsschema (pacman, APT, Windows-Installer), Bau aus `dev`
ohne Release-Tag, Veröffentlichung ins Repo der Testinstanz (`KIDCTL_REPO_TARGET`), Signatur.
