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
spec.md
CLAUDE.md
```

## Server

- Python 3, FastAPI, SQLAlchemy, SQLite, Jinja2 (serverseitig gerendert), wenig JavaScript.
- `bcrypt==4.0.1` pinnen (passlib[bcrypt] ist mit bcrypt 5.x inkompatibel:
  „password cannot be longer than 72 bytes“).
- Schemaänderungen über `app/migrate.py` (aufgerufen in `init_db()` vor `create_all()`):
  Spalte/Tabelle per `PRAGMA table_info` prüfen → `ALTER TABLE` → nach dem Commit verifizieren →
  bei Fehlschlag hart mit `RuntimeError` abbrechen. Diagnose-Logs mit DB-Pfad und Spalten
  vor/nach der Migration. `create_all()` allein ändert bestehende Tabellen nicht.
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

## Tests & Verifikation

- Jede Funktion vor Auslieferung end-to-end smoke-testen und im Changelog vermerken, was getestet
  wurde.
- Migrationen zusätzlich gegen eine synthetische Alt-Schema-Datenbank prüfen.
- Systemnahe Client-Teile (nftables, PAM, Mounts) nie ungefragt auf dem Entwicklungssystem
  scharf schalten – Dry-Run bzw. VM.
