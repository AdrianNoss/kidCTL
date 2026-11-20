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
| `REGISTRATION` | Nur mit `KIDCTL_MODE=saas`: `invite` (Standard, Registrierung nur mit Einladungscode aus dem Verwaltungsbereich), `open` (jeder) oder `closed`. Braucht E-Mail-Versand (`SMTP_*`). Im Einzelbetrieb immer geschlossen. |
| `KIDCTL_TRIAL_DAYS` | Nur SaaS: Testphase neuer Familien in Tagen (Standard 30). |
| `BILLING_WEBHOOK_SECRET` | Nur SaaS, optional: Geheimnis für den Abo-Webhook `POST /api/billing/webhook` (siehe unten). Leer = Webhook aus. |
| `SUPPORT_NOTIFY_EMAIL` | Nur SaaS, optional: Sammeladresse, die bei jedem neuen Ticket und jeder Antwort einer Familie einen Hinweis bekommt (zusätzlich zu Betreibern mit „Ticket-Mails“). |
| `TICKET_RETENTION_DAYS` | Nur SaaS: geschlossene Tickets nach so vielen Tagen löschen (Standard 365). |
| `INVOICE_TAX_MODE` | Rechnungen: `small_business` (Standard, Kleinunternehmer nach § 19 UStG, ohne USt) oder `standard` (19 % USt). Wird je Rechnung beim Ausstellen gespeichert. |
| `INVOICE_NUMBER_FORMAT` | Rechnungsnummer, Standard `RE-{YYYY}-{NNNN}` (fortlaufend je Jahr, lückenlos). |
| `INVOICE_DUE_DAYS` | Zahlungsziel in Tagen (Standard 14). |
| `ADMIN_ALLOW_NETS` | Optional. Verwaltungsbereich `/admin` nur aus diesen Netzen, kommagetrennt (z. B. `192.168.1.0/24,2001:db8::/32`); von anderswo antwortet er mit 404. Leer = von überall (Passwort + Authenticator bleiben Pflicht). |
| `TRUSTED_PROXIES` | Optional. Nur von diesen Adressen/Netzen wird `X-Forwarded-For` ausgewertet (für Login-Sperren und `ADMIN_ALLOW_NETS`), kommagetrennt. Standard: private Netze (`10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`, Loopback, `fc00::/7`). Empfohlen: feste Adresse des Traefik-Containers, z. B. `172.18.0.2/32`. |
| `TOTP_ISSUER` | Name in der Authenticator-App (Standard: Name aus „Erscheinungsbild“, sonst `KidCTL`) |
| `WEBAUTHN_RP_ID` | Domain, an die Passkeys gebunden sind (Standard: Host aus `BASE_URL`). Nur ändern, wenn Passkeys für eine übergeordnete Domain gelten sollen. |
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
eigenen Verwaltungsbereich. Er hat eigene Accounts, getrennt von den Eltern-Accounts. Die Navigation ist wie in der
Eltern-Oberfläche: am Computer eine Leiste links mit allen Bereichen, am Handy unten Familien, Tickets, Rechnungen
(bei *Support*: Protokoll) und „Mehr“ für den Rest.

```bash
docker compose exec kidctl python -m app.cli create-operator
```

Benutzername und Passwort eingeben, den QR-Code mit der Authenticator-App scannen. Passwort oder Authenticator
verloren: derselbe Befehl mit `--reset` setzt beides neu und beendet alle Sitzungen dieses Accounts.

Es gibt zwei **Rollen**: *Admin* (alles) und *Support* (Familien ansehen, Support-Ansicht, später Tickets – kein Abo,
keine Einladungscodes, keine Rechnungen). Neue Accounts sind *Admin*, sonst `--role support`; ändern mit
`python -m app.cli set-operator-role <name> admin|support`. Unter `/admin/profile` hinterlegt jeder Betreiber seine
E-Mail-Adresse, schaltet „Ticket-Mails erhalten“ ein, ändert sein Passwort, richtet einen neuen Authenticator ein und
meldet andere Sitzungen ab (Passwort- und Authenticator-Änderungen tun das automatisch, werden protokolliert und per
Mail gemeldet). Jede Änderung durch Betreiber, Webhook oder Hintergrundjobs
(Familienname, Abo, Ruhemodus, Einladungscodes, Löschung) steht im **Verwaltungsprotokoll** unter `/admin/log`
(filterbar nach Familie, Akteur und Art, für Familien unsichtbar, 2 Jahre aufbewahrt).

Die Übersicht zeigt Kennzahlen (Klick filtert), eine Suche nach Familiennummer oder -name, unter **Erweitert**
Filter nach Abo-Status, Tarif, Auffälligkeit, Inaktivität (über 7/30/90 Tage) und Registrierungszeitraum (aktive
Filter stehen als Chips unter der Suche und lassen sich einzeln entfernen) sowie eine sortierbare Tabelle (50 je Seite; Filter und Sortierung stehen in der Adresse und
lassen sich als Lesezeichen speichern). Außer Familienname und Rechnungsangaben sind das nur Metadaten: Anzahl Kinder
und Geräte, älteste Agent-Version, letzte Aktivität, Abo und Auffälligkeiten (Geräte offline, Agent veraltet,
Ruhemodus). Namen und Inhalte sind mit dem Schlüssel der jeweiligen
Familie verschlüsselt und dort nicht lesbar. Außerdem erzeugst du hier **Einladungscodes** für die Registrierung
(einmalig, 30 Tage gültig, mit Notiz). Mit E-Mail-Adresse geht der Code gleich per Mail hinaus, mit einem Link, der
ihn in die Registrierung einsetzt. „Erneut senden“ schickt einen neuen Code und macht den alten ungültig. Die Adresse
wird gelöscht, sobald der Code eingelöst oder abgelaufen ist. Neue Familien registrieren sich unter `/signup`:
E-Mail-Adresse (und Code) → Bestätigungslink per Mail (24 h) → Passwort und Authenticator → eigene Familie,
Inhaber-Account, angemeldet.

**Abo:** Neue Familien starten mit einer Testphase (`KIDCTL_TRIAL_DAYS`), Familien aus einem Einladungscode mit
„Tarif Kostenlos“ und alle Familien von vor dem Update im Tarif „Kostenlos“ (ohne Ablauf). Tarif, Status (Testphase,
aktiv, gekündigt, überfällig) und „bezahlt bis“ setzt du in der Übersicht über die Familiennummer; die Änderung wirkt
sofort. Nach Ablauf folgen 14 Tage Schonfrist mit Hinweisen (Mail an den Inhaber, Push, Banner), dann der Ruhemodus:
Die Agenten heben alle Einschränkungen auf, das WebUI zeigt nur noch Einstellungen (Abo, Export, Löschen). Nach
weiteren 90 Tagen ohne Abo wird die Familie automatisch gelöscht, mit Vorwarnung 30, 7 und 1 Tag vorher. Im
Einzelbetrieb gibt es das alles nicht.

**Familienname und Rechnungsangaben:** Der Inhaber trägt unter „Einstellungen“ den Namen der Familie und in der
Abo-Karte die Rechnungsangaben ein (Name bzw. Firma, Anschrift, Land, E-Mail für Rechnungen, optional USt-IdNr.).
Fehlen sie, obwohl sie gebraucht werden (bezahlter Tarif, Testphase in den letzten 7 Tagen), sieht die Familie einen
Hinweis und du in der Übersicht „Rechnungsangaben fehlen“. Auf der Familienseite siehst du die Angaben und kannst den
Namen ändern. Beides ist mit einem eigenen, vom Hauptschlüssel abgeleiteten Schlüssel verschlüsselt: im
Verwaltungsbereich lesbar, in einer Kopie der Datenbank allein nicht; es wird mit der Familie gelöscht.

Für einen Zahlungsanbieter gibt es einen neutralen Webhook (nur mit `BILLING_WEBHOOK_SECRET`):

```bash
curl -X POST https://kids.example.org/api/billing/webhook -H "Authorization: Bearer $BILLING_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"family_id": 12, "status": "active", "provider": "anbieter", "customer": "K-123", "paid_until": "2027-11-30"}'
```

`status` ist `active`, `canceled` oder `past_due`; nach dem ersten Aufruf genügt `customer` statt `family_id`.

**Tickets:** Eltern finden im Menü „Support“ (auch im Ruhemodus) ihre Tickets und legen neue an (Betreff, Kategorie
Technik, Abo & Rechnung oder Sonstiges, Nachricht). Im Verwaltungsbereich stehen sie unter „Tickets“ (Standard:
offene, älteste zuerst; filterbar nach Status, Kategorie, Familie und Betreff). Antworten setzen den Status auf
„wartend“ (wählbar „in Bearbeitung“ oder „geschlossen“); eine **interne Notiz** sieht nur der Support. Antwortet die
Familie, geht das Ticket auf „in Bearbeitung“, auch ein geschlossenes – bis 14 Tage nach dem Schließen. Wartet ein
Ticket 14 Tage auf die Familie, wird es geschlossen. Hinweise per Mail bzw. Push enthalten nie den Inhalt, nur einen
Link. Betreff und Nachrichten sind wie die Rechnungsangaben mit dem Plattform-Schlüssel verschlüsselt; Tickets werden mit
der Familie gelöscht und sind nicht im Familien-Export.

**Rechnungen** (nur *Admin*): Unter „Rechnungen → Rechnungs-Einstellungen“ trägst du Absender, Steuernummer bzw.
USt-IdNr., Kontakt, Bankverbindung, Fußzeile und die Preisliste ein (protokolliert). Auf der Familienseite erstellst
du mit „Rechnung erstellen“ einen Entwurf aus einer Vorlage: Leistungszeitraum ab dem Tag nach „bezahlt bis“, Positionen
frei änderbar (negative Beträge für Rabatte). „Ausstellen und senden“ vergibt die nächste Nummer, friert Rechnungsadresse,
Beträge und Steuermodus ein, erzeugt das PDF und schickt es an die Rechnungs-E-Mail der Familie. Danach ist die Rechnung
unveränderlich; Korrekturen nur per „Stornieren“ (Storno-Rechnung mit eigener Nummer und Bezug). „Als bezahlt
markieren“ setzt das Abo aktiv und „bezahlt bis“ auf das Ende des Leistungszeitraums – ein Ruhemodus endet sofort.
Eltern sehen ihre Rechnungen unter „Einstellungen → Abo“. Die Liste „Rechnungen“ zeigt Summen je Jahr und exportiert
ein Jahr als CSV bzw. ZIP mit allen PDFs. Rechnungen bleiben beim Löschen einer Familie erhalten und werden
automatisch gelöscht, wenn die Aufbewahrungsfrist abgelaufen ist (8 Jahre ab Ende des Ausstellungsjahres).

**Erscheinungsbild** (nur *Admin*): Unter „Erscheinungsbild“ legst du einen eigenen Namen, ein Logo und ein App-Icon
fest (PNG, JPEG, WebP, GIF oder ICO; Logo wird auf 512 × 128, das Icon auf 512 × 512 Pixel gebracht). Das gilt für die
Eltern-Oberfläche, die App auf dem Handy, Anmeldung und Registrierung, Push-Nachrichten und alle Mails an Familien; mit
eigenem Namen steht überall „powered by KidCTL“, in Mails als Fußzeile. Die Programme auf den Kinderrechnern heißen
weiterhin KidCTL. Den Absendernamen der Mails setzt du in `SMTP_FROM` (z. B. `Familienwächter <noreply@example.org>`).
Änderungen werden protokolliert und sind nach spätestens einer Minute überall sichtbar. Unter „Homepage“ setzt du den
Link unter den Anmeldeseiten (nur `https://`, Linktext auf Deutsch und Englisch); leer zeigt er auf die Projektseite.

**Mails:** Alle Mails haben dasselbe Layout (hell und dunkel, mit Textfassung) mit dem Logo als eingebettetem Bild –
ohne externe Bilder oder Tracking. Im Block „Anbieter“ auf der Seite „Erscheinungsbild“ trägst du Name, Anschrift,
Kontakt-E-Mail und Links zu Impressum, Datenschutz und Neuigkeiten ein; sie erscheinen in der Fußzeile jeder Mail
(leere Felder entfallen, „Anleitungen“ zeigt auf das Wiki). Unter „Mails“ siehst du jeden Mailtyp mit Beispieldaten auf
Deutsch und Englisch und schickst ihn mit „Testmail an mich“ an die Adresse aus deinem Profil.

**Newsletter** (nur *Admin*, nur SaaS): Unter „Newsletter“ schreibst du Neuigkeiten (neue Versionen, Funktionen) mit
Betreff und Text auf Deutsch, optional auf Englisch. Den Text formatierst du mit Markdown: `##`/`###` für
Überschriften, `**fett**`, `*kursiv*`, Listen mit `-` oder `1.`, Links `[Text](https://…)` und `---` als Trennlinie
(Bilder, HTML und Code werden nicht übernommen). Nach dem Speichern zeigt „Vorschau“ die fertige Mail je Sprache.
„Speichern und Test an mich“ schickt beide Fassungen an die Adresse aus deinem Profil. „An N Familien senden“ verschickt im Hintergrund an alle Eltern mit bestätigter Adresse in
Familien, die zugestimmt haben – jeweils in ihrer Sprache (ohne englischen Text: Deutsch), mit Anrede und Abmeldelink
(auch „Abmelden mit einem Klick“ im Mailprogramm). Danach ist der Newsletter unveränderlich und bleibt mit Zahl der
Familien und Mails im Archiv; der Versand steht im Verwaltungsprotokoll. Familien stimmen selbst zu (Opt-in, Standard
aus): bei der Registrierung oder unter „Einstellungen → Neuigkeiten per E-Mail“; dort und über den Link in jeder Mail
bestellen sie wieder ab. Bestehende Familien haben nicht zugestimmt.

> [!IMPORTANT]
> Die Rechnungsvorlage ist noch nicht steuerlich geprüft. Muster beider Steuermodi erzeugt
> `python dev/sample_invoices.py` – vor der ersten echten Rechnung dem Steuerberater vorlegen.

**Support-Ansicht:** Der Inhaber einer Familie gibt unter „Einstellungen → Support-Zugriff“ für 1, 24 oder 72 Stunden
frei; jedes Elternteil kann die Freigabe jederzeit beenden. Solange sie gilt, steht in der Übersicht bei der Familie
„Ansehen“. Du siehst dann ihre Seiten wie ein Elternteil, aber nur lesend (keine Änderungen, keine Nachrichten, keine
Profile), mit Banner; die Ansicht endet nach 2 Stunden, mit der Freigabe oder beim Abmelden. Jeder Seitenaufruf
wird protokolliert und ist für die Familie in den Einstellungen sichtbar. Die Sitzung gilt 12 Stunden; mit `ADMIN_ALLOW_NETS` lässt sich der Bereich
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

Die Anmeldung läuft in zwei Schritten: erst E-Mail-Adresse (oder Benutzername) und Passwort, dann der
Authenticator-Code. Für den zweiten Schritt bleiben 5 Minuten und 5 Versuche, danach beginnt die Anmeldung von vorn.
Der Verwaltungsbereich (`/admin/login`) funktioniert genauso. Mit einem Passkey entfallen beide Schritte
(siehe [WebUI](webui.de.md#anmelden-und-passkeys)); Betreiber legen ihn unter Verwaltung → Profil an.

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
