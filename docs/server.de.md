# Server

## Code holen und Datenordner anlegen

```bash
git clone https://github.com/AdrianNoss/kidCTL.git /srv/kidctl
cd /srv/kidctl
mkdir -p data
sudo chown 10001:10001 data     # der Container läuft als Benutzer 10001
```

`./data` enthält die Datenbankdateien von PostgreSQL (`data/postgres`, gehören dem Datenbank-Container), die
täglichen Datenbank-Backups, Sperrlisten und das Paket-Repository (Pacman und APT).

> [!IMPORTANT]
> **Den Ordner extern sichern** – für die Datenbank zählen dabei die Backups in `data/backups`, nicht die
> laufenden Dateien in `data/postgres`. Siehe [Backup und Wiederherstellung](backup.de.md).

## Konfiguration (`.env`)

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # Ausgabe als SECRET_KEY eintragen
```

| Variable | Bedeutung |
|---|---|
| `SECRET_KEY` | Pflicht. **Nie ändern**, sonst sind alle Sessions und Geräte-Token ungültig (Geräte neu koppeln). |
| `POSTGRES_PASSWORD` | Pflicht. Passwort der Datenbank, nur Buchstaben, Ziffern, `-` und `_` (z. B. `secrets.token_urlsafe(32)`). Die Datenbank läuft im Container `kidctl-db` und ist nur für den Server erreichbar. |
| `BASE_URL` | Öffentliche Adresse, z. B. `https://kids.example.org` |
| `KIDCTL_MODE` | `single` (Standard, eine Familie) oder `saas` (viele Familien; E-Mail ist Pflicht und ersetzt den Benutzernamen) |
| `TOTP_ISSUER` | Name in der Authenticator-App (Standard `KidCTL`) |
| `SOURCE_URL` | Link „Quellcode“ in der Fußzeile (AGPL-3.0 §13); bei eigenen Änderungen auf den eigenen Fork setzen. Standard: das KidCTL-Repo |
| `TZ_HOME` | Zeitzone für Tageskontingente und Zeitfenster (Standard `Europe/Berlin`) |
| `VAPID_*` | Web Push, siehe [Web Push](#web-push-empfohlen). Ohne Schlüssel gibt es keine Push-Benachrichtigungen. |
| `SMTP_*` | E-Mail für „Passwort vergessen“, Sicherheitshinweise und Nutzungsbericht (optional), siehe [E-Mail](#e-mail-und-konto-wiederherstellung-empfohlen) |

## Traefik und Start

In `docker-compose.yml` anpassen:

- `Host(\`kids.nossco.de\`)` → eigene Subdomain
- Netzwerkname unter `networks.traefik.name` und `traefik.docker.network` → tatsächlicher Name
  (`docker network ls`)

```bash
docker compose up -d --build
docker compose logs -f kidctl      # „Application startup complete“
```

Test: `https://kids.example.org` zeigt die Anmeldeseite.

## Web Push (empfohlen)

Push braucht man für Chat-Nachrichten, Verlängerungsanfragen und Alarme auf dem Handy.

```bash
docker compose run --rm kidctl python -m app.cli vapid-keys
```

Die Ausgabe in `.env` eintragen, `VAPID_SUBJECT=mailto:<eigene Adresse>` setzen, dann
`docker compose up -d`.

## Ersten Eltern-Account anlegen

```bash
docker compose exec kidctl python -m app.cli create-parent
```

Benutzername (bzw. mit `KIDCTL_MODE=saas` die E-Mail-Adresse) und Passwort eingeben, dann den angezeigten
QR-Code mit der Authenticator-App scannen. Dieser erste Account ist **Inhaber** der Familie: Nur er legt weitere
Eltern-Accounts im WebUI unter „Eltern“ an oder entfernt sie.

## Handy einrichten

1. `https://kids.example.org` im Handy-Browser öffnen und anmelden.
2. „Zum Startbildschirm hinzufügen“ bzw. „App installieren“ (PWA). Unter iOS funktioniert Push nur
   in der installierten App.
3. In der App unter „Benachrichtigungen“ Push aktivieren und „Test“ drücken.

## E-Mail und Konto-Wiederherstellung (empfohlen)

In `.env` (danach `docker compose up -d`):

| Variable | Bedeutung |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | Mailserver, z. B. Port 587 |
| `SMTP_SECURITY` | `starttls` (Port 587), `ssl` (Port 465) oder `none` (nur im lokalen Netz) |
| `SMTP_USER`, `SMTP_PASSWORD` | Zugangsdaten, leer lassen ohne Anmeldung |
| `SMTP_FROM` | Absender, z. B. `KidCTL <kidctl@example.org>` |

Jedes Elternteil trägt unter **Profil** (Symbol oben rechts) seine E-Mail-Adresse ein und wählt dort die
Sprache (Automatisch nach Browser, Deutsch oder English) für WebUI, Mails und Push. Mit SMTP kommt ein
Bestätigungslink (48 Stunden gültig); erst danach meldet man sich mit der Adresse an und bekommt Hinweise,
Berichte und „Passwort vergessen“. Jede Adresse kann nur einmal verwendet werden. Der Benutzername bleibt zum
Anmelden erhalten. Im Profil lassen sich auch Anzeigename, Passwort und der Authenticator ändern.

| Problem | Lösung |
|---|---|
| Passwort vergessen | Anmeldeseite → „Passwort vergessen?“. Der Link ist 30 Minuten gültig und setzt **nur das Passwort** zurück. Zum Anmelden braucht man weiterhin den Authenticator. |
| Authenticator verloren (z. B. neues Handy) | Ein anderes Elternteil: „Eltern“ → „Authenticator zurücksetzen“. Der alte Authenticator ist sofort ungültig. Den angezeigten Einrichtungslink (24 h) persönlich weitergeben, er verlangt zusätzlich das Passwort. |
| Nur ein Eltern-Account oder beides verloren | Auf dem Server: `docker compose exec kidctl python -m app.cli reset-totp <benutzer>` bzw. `reset-password <benutzer>` |

Bei Passwort- oder Authenticator-Änderungen bekommt das Elternteil einen Hinweis per E-Mail, und
seine anderen Sitzungen werden beendet.

**Nutzungsbericht:** Unter **Profil → Nutzungsbericht per E-Mail** lässt sich je Elternteil ein
wöchentlicher (montags) oder monatlicher (am 1.) Bericht aktivieren, jeweils ab 7 Uhr. „Vorschau“ zeigt
ihn im Browser, „Jetzt senden“ verschickt ihn sofort zum Ausprobieren.
