# CLAUDE.md – kidctl (Parental Control für Arch Linux)

Stabile Projekt- und Coding-Konventionen. Fachliche Anforderungen und Changelog stehen in
`spec.md` – vor jeder Änderung lesen. Die Anforderungen sind per /grill-me abgestimmt und final;
Abweichungen nur nach Rückfrage.

## Arbeitsweise

- `spec.md` ist die lebende Spezifikation: neue Features/Änderungen dort eintragen und im
  Abschnitt „Changelog“ datiert vermerken (Format `- YYYY-MM-DD: …`).
- Erst Anforderungen klären, dann umsetzen. Bei Unklarheiten fragen statt raten.
- Minimal-invasive Änderungen: nur anfassen, was für die Aufgabe nötig ist.
- Pragmatisch statt over-engineered.
- Skripte, die Dateien oder Systemkonfiguration ändern, laufen standardmäßig als Dry-Run;
  echte Änderungen nur mit `--apply`, vorher `.bak`-Backup.
- Antworten und Doku auf Deutsch, knapp, Markdown (BookStack-tauglich).

## Repo-Struktur (Ziel)

```
server/        FastAPI-App, Templates, Static (PWA), Dockerfile
agent/         Root-Agent (systemd), PAM-Helfer, nftables-/Resolver-Steuerung
tray/          PySide6-Tray-App
common/        Geteilte Datenmodelle (Policy-Schema, WebSocket-Nachrichten, Protokollversion)
packaging/     PKGBUILD, systemd-Units, PAM-, nftables-, Browser-Policy-, Mount-Konfiguration
dev/vm/        Test-VM (QEMU/KVM, Arch + KDE Plasma Wayland) für systemnahe Client-Tests
spec.md
CLAUDE.md
```

## Server

- Python 3, FastAPI, SQLAlchemy, PostgreSQL (psycopg 3), Jinja2 (serverseitig gerendert), wenig JavaScript.
- `bcrypt==4.0.1` pinnen (passlib[bcrypt] ist mit bcrypt 5.x inkompatibel:
  „password cannot be longer than 72 bytes“).
- Schemaänderungen nur über Alembic-Migrationen in `server/app/alembic/versions/` (`init_db()` führt beim Start
  `upgrade head` aus): Modell ändern → Revision erzeugen (`command.revision(alembic_config(), autogenerate=True)`),
  prüfen und von Hand nachbessern (Standardwerte bestehender Zeilen, Datenmigration) → `alembic check` muss „keine
  Änderungen“ melden. Kein `create_all()` im Betrieb. SQLite nur noch als Quelle der einmaligen Übernahme
  (`app/sqlite_import.py`, `cli import-sqlite`).
- Tests laufen gegen PostgreSQL (Entwicklung: Podman-Container `kidctl-pg` auf 127.0.0.1:55432, Datenbank
  `kidctl_test`, wird bei Bedarf angelegt; `dev/devserver.sh` startet den Container).
- Sicherheit (öffentlich erreichbar): TOTP-Pflicht, Login-Rate-Limiting, CSRF-Schutz auf allen
  Formularen/Aktionen, 30-Tage-Sessions, `SECRET_KEY` nur aus `.env` (kein Default in Produktion).
- Konfiguration ausschließlich über Env-Variablen in `.env`.
- Bootstrap des ersten Eltern-Accounts per CLI-Skript (interaktiv oder per Env-Var).

## Docker / Traefik

- Docker Compose hinter dem bestehenden Traefik.
- Traefik-Labels in `=`-Syntax, nicht YAML-Doppelpunkt-Format.
- Router mit Middleware `secHeaders@file` und certresolver `letsencrypt`.
- WebSocket-Endpunkt muss durch Traefik funktionieren (kein Sonder-Entrypoint nötig).
- Bind-Mounts statt benannter Volumes: `./data:/data` (DB + Laufzeitdaten). Backup extern.
- Jeder Service mit `restart: unless-stopped` und explizitem `container_name`.
- Traefik-Netzwerkname in `docker-compose.yml` an den tatsächlichen Namen anpassen.
- Pacman-Repo als statisches Verzeichnis im selben Stack ausliefern.

## Client (Agent + Tray)

- Python 3 / asyncio, D-Bus über `dbus-next`, Prozess-Events über Netlink `PROC_EVENTS`.
- Nur der Root-Agent hält Geräte-Token (`root:root 0600`) und Serververbindung.
- Tray-App spricht ausschließlich über Unix-Socket mit dem Agenten; Agent prüft `SO_PEERCRED`.
- Zielumgebung ausschließlich KDE Plasma auf Wayland.
- Zeitdauer immer über monotone Uhr (`CLOCK_BOOTTIME`), Systemuhr nur für Datum/Zeitfenster.
- Durchsetzung muss offline mit gecachter Policy funktionieren.
- Grundregel: nicht zugeordnete Linux-User werden nie eingeschränkt – niemals Admin-Accounts
  aussperren.
- Abhängigkeiten bevorzugt aus den offiziellen Arch-Repos (z. B. `pyside6`), damit das PKGBUILD
  ohne pip auskommt.

## Protokoll

- Gemeinsame Nachrichtenformate in `common/`, versioniert.
- Protokollversion im WebSocket-Handshake; Server unterstützt mindestens eine ältere Version.

## Website (`website/`)

- Hugo, eigenes Theme, kein Node. Inhalte immer **zweisprachig** (`.de.md` / `.en.md`, YAML-Felder `de`/`en`).
- **News:** Bei jeder Versionsänderung prüfen: neue Minor-Version (x.Y.0) oder sicherheitsrelevanter Patch →
  im selben Commit eine News unter `website/content/news/` anlegen (DE+EN, für Nutzer formuliert, nicht der
  technische Changelog). Normale Patches: keine News. Meilenstein-News nur auf ausdrücklichen Wunsch.
- **Release Notes im Server:** Bei jeder Versionsänderung oben in `server/app/release_notes.py` einen Eintrag ergänzen
  (Version, Datum, 2–4 Stichpunkte DE/EN für Eltern); Eltern sehen ihn nach dem Update auf der Übersicht.
- **Roadmap:** Wird in `spec.md` ein Feature entschieden, begonnen oder abgeschlossen, `website/data/roadmap.yaml`
  anpassen (Status, ggf. Version). Keine Termine, keine Abrechnungs-/Rechtsdetails.
- **Feature-Texte und README:** Neue Plattform oder neues bzw. geändertes nutzersichtbares Feature → README.md,
  README.de.md und die Funktionsliste der Website im selben Commit nachziehen.
- **Screenshots:** Sichtbare Änderung an der Eltern-WebUI → `dev/screenshots.py` laufen lassen und die geänderten
  Bilder mit committen. Client-Screenshots nur auf Anweisung.
- Kein Tracking, keine externen Ressourcen (Schriften/Icons lokal).
- Lokal prüfen: `cd website && hugo server` – vor dem Commit einmal `hugo --minify` ohne Fehler.
- **Doku (`docs/`) und README sind zweisprachig mit exakt gleichem Inhalt.** Quelle ist Deutsch (`x.de.md`,
  `README.de.md`); Englisch (`x.md`, `README.md`) im selben Commit nachziehen. Vor dem Commit
  `python dev/check_docs_parity.py` – muss fehlerfrei sein.
- Doku-Dateien ohne Frontmatter; Titel = erste `#`-Überschrift. Neue oder umbenannte Seite → in
  `website/data/docs.yaml` eintragen.
- Links zwischen Doku-Seiten relativ (`server.md#…`), damit sie auf GitHub funktionieren. Hinweise als
  GitHub-Alerts (`> [!NOTE]` usw.).
- Bedienanleitungen für Self-Hoster gehören nach `docs/`, nicht in Komponenten-READMEs. Komponenten-READMEs,
  Spec und Code-Kommentare bleiben deutsch.
- Sichtbare Änderung an einem in `docs/img/` abgebildeten WebUI-Schritt → `dev/screenshots.py` laufen lassen.
- In Code, Skripten und Templates auf das Wiki verlinken, nicht auf `blob/main/docs/…` (Ausnahme: Fehlermeldungen
  nennen zusätzlich den Dateipfad).

## Sicherheit

- Meldeweg und Regeln stehen in `SECURITY.md`/`SECURITY.de.md` (zweisprachig, Paritäts-Check). Sicherheitsthemen
  nie in öffentlichen Issues, Commit-Messages oder News im Detail beschreiben, bevor der Fix veröffentlicht ist.
- **Neue Restlücke** (bewusst nicht umgesetzt oder bekannte Umgehung) → im selben Commit in
  `docs/limitations.de.md` und `docs/limitations.md` eintragen; die Spec verweist nur.
- **Sicherheitsfix:** Patch-Version erhöhen; Text für das GitHub Security Advisory vorbereiten (betroffene/behobene
  Version, Schwere, Beschreibung ohne Ausnutzungsanleitung, Dank laut Melder) und Adrian zum Veröffentlichen
  übergeben; Security-News laut Website-Regel, mit Link aufs Advisory.
- Keine Geheimnisse ins Repo (GPG-/Signaturschlüssel, Tokens, `.env`); Push Protection ist aktiv – Treffer nie
  per Ausnahme freigeben, sondern Geheimnis entfernen und ggf. austauschen.

## Verwaltungsbereich (`/admin`)

- Rollen *Admin*/*Support*: Rechte immer serverseitig per Route prüfen, Tests für jede Admin-Route mit beiden Rollen.
- Jede ändernde Aktion eines Betreibers, des Webhooks oder eines Hintergrundjobs an Familien, Tickets, Rechnungen oder
  Rechnungs-Einstellungen schreibt einen Eintrag ins Verwaltungsprotokoll.
- Listen (Familien, Tickets, Rechnungen, Protokoll): Suche/Sortierung/Filter/Blättern im Server, Parameter in der URL,
  ohne JavaScript.
- Ausnahme von „Konfiguration nur über `.env`“: Erscheinungsbild (Name, Logo, App-Icon; Whitelabel) sowie Absender, Bankverbindung, Fußzeile und Preisliste der Rechnungen
  liegen in der Datenbank (Admin-Seite, protokolliert). Steuermodus, Nummernformat und Fälligkeit bleiben in `.env`.

## Rechnungen

- Ausgestellte Rechnungen sind unveränderlich – nie per Migration, Skript oder Code ändern; Korrektur nur per
  Storno-Rechnung. Nummern lückenlos (Vergabe in derselben Transaktion wie das Ausstellen).
- Rechnungen und ihre PDFs nie mit dem Familienschlüssel verschlüsseln und nie beim Löschen einer Familie entfernen;
  Löschung nur über die Aufbewahrungsfrist.
- Beträge als ganze Cent (Integer), nie als Float.

## E-Mails

- Jede Mail ist ein Template, das von `templates/mail/base.html` erbt, und hat immer eine Textfassung.
  Nur Tabellenlayout und Inline-Styles; keine externen Bilder, Schriften oder Skripte; Logo nur per CID.
- Neuer Mailtyp → Beispieldaten für `/admin/mail-preview` und Test (DE/EN) im selben Commit.
- Tickets und Hinweise: Mails enthalten nur Hinweis + Link, keine Inhalte (Ausnahme: Rechnungen mit PDF,
  Newsletter, Nutzungsbericht).

## Tray-App unter Windows

- Windows-Design nur in `winstyle.py`/QSS; Logik und Texte gemeinsam mit Linux. Keine harten Farben in Fenstern –
  Farben aus Palette bzw. Stilschicht.
- Sichtbare Änderung an einem Fenster → `dev/winshots.py` in der VM laufen lassen und Bilder prüfen.

## Spielerkennung Windows

- Launcher-Formate nur anhand echter Installationen (VM-Snapshot „launchers“) umsetzen und als anonymisierte
  Testdaten ablegen; keine angenommenen Formate.

## Anmeldung

- Login immer zweistufig über Login-Tickets; nie verraten, ob ein Konto existiert (gleiche Antwort, `dummy_verify`).
- Passkeys (`webauthn`): Challenges serverseitig, einmalig, kurzlebig; `userVerification=required`; Origin/RP-ID aus
  der Konfiguration, nie aus dem Request.
- Änderungen an Anmeldedaten (Passwort, Authenticator, Passkeys) nur nach frischer Bestätigung und immer mit
  Hinweis-Mail.
- Der klassische Login (Passwort + Authenticator) muss ohne JavaScript funktionieren.

## Oberfläche

- Eltern- und Verwaltungsbereich nutzen dieselben Komponenten (Navigation, Karten, Buttons, Formulare); keine
  Inline-Styles, Korrekturen zentral im CSS.
- Sichtbare Änderung an Seiten → Design-Review-Skript für die betroffenen Seiten laufen lassen.

## Tests & Verifikation

- Jede Funktion vor Auslieferung end-to-end smoke-testen und im Changelog vermerken, was getestet
  wurde.
- Migrationen zusätzlich gegen eine synthetische Alt-Schema-Datenbank prüfen.
- Systemnahe Client-Teile (nftables, PAM, Mounts) nie ungefragt auf dem Entwicklungssystem
  scharf schalten – Dry-Run bzw. VM.
