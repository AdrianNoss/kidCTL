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
| `KIDCTL_MASTER_KEY` | Pflicht. Hauptschlüssel der Verschlüsselung (Namen, Chat, Protokoll, Statistik je Familie). Erzeugen mit `docker compose run --rm kidctl python -m app.cli master-key`. **Getrennt sichern** (Passwortmanager): ohne ihn sind die Daten auch aus Backups nicht wiederherstellbar. Nie ändern. |
| `POSTGRES_PASSWORD` | Pflicht. Passwort der Datenbank, nur Buchstaben, Ziffern, `-` und `_` (z. B. `secrets.token_urlsafe(32)`). Die Datenbank läuft im Container `kidctl-db` und ist nur für den Server erreichbar. |
| `BASE_URL` | Öffentliche Adresse, z. B. `https://kids.example.org` |
| `KIDCTL_RELOCATE_TO` | Nur beim Server-Umzug: neue Adresse (`https://…`). Jeder Agent ab 0.33 wechselt dorthin, sobald der neue Server sein Geräte-Token anerkennt (gleiche Datenbank, gleicher `SECRET_KEY`); sonst bleibt er beim alten. Auf dem **alten** Server setzen, bis alle Geräte umgezogen sind. |
| `KIDCTL_MODE` | `single` (Standard, eine Familie) oder `saas` (viele Familien; E-Mail ist Pflicht und ersetzt den Benutzernamen) |
| `ADMIN_ALLOW_NETS` | Optional. Verwaltungsbereich `/admin` nur aus diesen Netzen, kommagetrennt (z. B. `192.168.1.0/24,2001:db8::/32`); von anderswo antwortet er mit 404. Leer = von überall (Passwort + Authenticator bleiben Pflicht). |
| `TOTP_ISSUER` | Name in der Authenticator-App (Standard `KidCTL`) |
| `SOURCE_URL` | Link „Quellcode“ in der Fußzeile (AGPL-3.0 §13); bei eigenen Änderungen auf den eigenen Fork setzen. Standard: das KidCTL-Repo |
| `TZ_HOME` | Zeitzone für Tageskontingente und Zeitfenster (Standard `Europe/Berlin`) |
| `SECURITY_CONTACT` | Optional. Eigener Sicherheitskontakt (E-Mail-Adresse oder https-URL) für `/.well-known/security.txt`; wird vor dem Projektkontakt genannt |
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

Test: `https://kids.example.org` zeigt die Anmeldeseite. Unter `https://kids.example.org/.well-known/security.txt`
liefert der Server die Sicherheitskontakte (RFC 9116, siehe [Sicherheitsrichtlinie](../SECURITY.de.md)).

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
QR-Code mit der Authenticator-App scannen. Dieser erste Account ist **Inhaber** der Familie: Nur er lädt im WebUI unter
„Eltern“ weitere Elternteile ein (Einladungslink, 7 Tage gültig; die eingeladene Person legt Passwort und
Authenticator selbst fest) oder entfernt sie.

## Verwaltungsbereich (Betreiber)

Für den Betrieb mit mehreren Familien (`KIDCTL_MODE=saas`) gibt es unter `https://kids.example.org/admin` einen
eigenen Verwaltungsbereich. Er hat eigene Accounts, getrennt von den Eltern-Accounts:

```bash
docker compose exec kidctl python -m app.cli create-operator
```

Benutzername und Passwort eingeben, den QR-Code mit der Authenticator-App scannen. Passwort oder Authenticator
verloren: derselbe Befehl mit `--reset` setzt beides neu und beendet alle Sitzungen dieses Accounts.

Der Bereich zeigt nur Metadaten: Familien mit Anzahl Eltern, Kinder und Geräte, Agent-Versionen, letzte Aktivität
und Auffälligkeiten (Geräte offline, Agent zu alt, Ruhemodus). Namen und Inhalte sind mit dem Schlüssel der jeweiligen
Familie verschlüsselt und dort nicht lesbar. Außerdem erzeugst du hier **Einladungscodes** für die Registrierung
(einmalig, 30 Tage gültig, mit Notiz). Die Sitzung gilt 12 Stunden; mit `ADMIN_ALLOW_NETS` lässt sich der Bereich
auf bestimmte Netze beschränken.

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
| Nur ein Eltern-Account oder beides verloren | Auf dem Server: `docker compose exec kidctl python -m app.cli reset-totp <user>` bzw. `reset-password <user>` |

Bei Passwort- oder Authenticator-Änderungen bekommt das Elternteil einen Hinweis per E-Mail, und
seine anderen Sitzungen werden beendet.

**Nutzungsbericht:** Unter **Profil → Nutzungsbericht per E-Mail** lässt sich je Elternteil ein
wöchentlicher (montags) oder monatlicher (am 1.) Bericht aktivieren, jeweils ab 7 Uhr. „Vorschau“ zeigt
ihn im Browser, „Jetzt senden“ verschickt ihn sofort zum Ausprobieren.
