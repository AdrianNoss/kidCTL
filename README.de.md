# KidCTL

[English](README.md) · **Deutsch**

Selbst gehostete Kindersicherung für **Arch Linux, Debian und Ubuntu** (auch Mint, Pop!_OS) **mit KDE Plasma oder GNOME (Wayland)**. Ein zentraler Server
mit Eltern-WebUI (auch als App aufs Handy) steuert einen Root-Agenten auf jedem Kinderrechner. Alle
Daten bleiben auf dem eigenen Server, es gibt keine Cloud und kein Konto bei Dritten.

## Funktionen

- **Bildschirmzeit**:
  - Tageskontingent und Zeitfenster je Wochentag, Zählung über alle Rechner eines Kindes.
  - Warnungen vor Ablauf, danach Abmeldung und Login-Sperre (SDDM, GDM und TTY).
  - Funktioniert auch offline mit der zuletzt bekannten Regel.
- **Verlängerungen und Chat**: Das Kind fragt in der Tray-App nach mehr Zeit oder schreibt eine
  Nachricht, die Eltern antworten per Push auf dem Handy.
- **Programm-Whitelist**:
  - Modi „Nur protokollieren“ oder „Sperren“.
  - Katalog der installierten Apps, Flatpak (systemweit) und Steam.
  - Eigene Programme im Home sind durch noexec blockiert.
- **Website-Filter** (DNS):
  - Whitelist mit Dienst-Bundles (YouTube, Wikipedia, Lern-Apps, Microsoft 365) oder Blacklist mit
    Sperrlisten (HaGeZi, OISD, StevenBlack).
  - SafeSearch je Kind.
  - DNS-over-HTTPS bzw. DoT der Browser sind gesperrt.
- **Auswertung**: Statistik pro Tag und Programm sowie ein Protokoll gesperrter Programme und
  Domains. Ein Verlauf erlaubter Websites wird **nicht** gespeichert.
- **Alarme per Push**:
  - Gerät zu lange offline.
  - Schutzmaßnahme manipuliert, z. B. PAM, nftables oder noexec.
- **Sicherheit**:
  - Eltern-Login nur mit TOTP, Rate-Limiting, CSRF-Schutz.
  - Signiertes Paket-Repository für Updates (Pacman und APT).

## Architektur

```
 Eltern (Browser / PWA)        Kinderrechner (Arch/Debian, Plasma/GNOME)
          │ HTTPS                          ┌──────────────────────────────┐
          ▼                                │ kidctl-agent (root, systemd) │
 ┌──────────────────────┐   WebSocket      │  Zeit · PAM · Prozesse ·     │
 │ Server (FastAPI)     │◄─────────────────┤  Unbound/nftables · noexec   │
 │ Docker hinter Traefik│   (ausgehend)    └──────────────┬───────────────┘
 │ SQLite · Web Push    │                                 │ Unix-Socket
 │ /repo (Pacman-Repo)  │                  ┌──────────────┴───────────────┐
 └──────────────────────┘                  │ Tray-App (PySide6, Kind)     │
                                           └──────────────────────────────┘
```

- **Server**: Python/FastAPI, SQLAlchemy/SQLite, Jinja2. Er liefert auch das signierte
  Pacman-Repository aus.
- **Agent**: Python/asyncio als root.
  - Hält als Einziger das Geräte-Token.
  - Setzt alles auf Systemebene durch: logind, PAM, Netlink-Prozessereignisse, Unbound je Kind,
    nftables, systemd-Mounts.
- **Tray-App**: zeigt Restzeit und Chat an. Sie ist rein kosmetisch, ihr Beenden ändert nichts an
  der Durchsetzung.

Grundregel: root und Accounts in `wheel`/`sudo`/`admin` werden **nie** eingeschränkt.

## Installation

Quellcode: <https://github.com/AdrianNoss/kidCTL>

Schritt für Schritt in **[docs/installation.md](docs/installation.md)**: Server, Paket-Repository,
Kinderrechner und WebUI. Kurzfassung:

```bash
# Server
cp .env.example .env            # SECRET_KEY, BASE_URL setzen; Traefik-Netz in docker-compose.yml
docker compose up -d --build
docker compose exec kidctl python -m app.cli create-parent

# Kinderrechner (nach Einrichtung des Repos)
sudo pacman -S kidctl kidctl-plasma   # bzw. kidctl-gnome (bei beiden Desktops beide)
sudo apt install kidctl kidctl-gnome  # Debian/Ubuntu (bzw. kidctl-plasma)
sudo kidctl enroll https://kids.example.org <KOPPLUNGSCODE>
sudo systemctl enable --now kidctl-agent.service
```

Danach die Rechner gegen physischen Zugriff härten: **[docs/haertung.md](docs/haertung.md)**.

## Grenzen

- Nur KDE Plasma und GNOME auf Wayland. Andere Desktops und X11 werden nicht unterstützt.
- Unter GNOME keine automatische Anmeldung (GDM) für Kinder – bei Login-Sperre bliebe der Bildschirm schwarz.
- Kein Schutz gegen physischen Zugriff ohne Härtung, also gegen Live-USB oder Bootmenü.
- Keine Leerlauferkennung. Kein `flatpak --user`. Steam ist nur als Ganzes steuerbar.
- VPNs werden nicht aktiv erkannt.

Die vollständige Liste steht in [`spec.md`](spec.md) unter „Bewusst nicht umgesetzt / Restlücken“.

## Repository-Struktur

| Pfad | Inhalt |
|---|---|
| `server/` | FastAPI-App, Templates, PWA, Dockerfile, Tests |
| `agent/` | Root-Agent (`kidctl_agent`), Tests |
| `tray/` | Tray-App (`kidctl_tray`) |
| `common/` | Gemeinsames, versioniertes Protokoll (`kidctl_common`) |
| `packaging/arch/` | PKGBUILD, systemd-Units, Install-Skript |
| `packaging/pam`, `dns`, `mounts`, `sddm`, `gnome` | Login-Sperre, DNS-Filter und Browser-Policies, noexec, Anmeldebildschirm, GNOME-Anpassungen |
| `packaging/repo/`, `debian` | Skripte für das signierte Paket-Repository, `.deb`-Bau für Debian/Ubuntu |
| `dev/` | Entwicklungsserver, WebUI-Testskript, Test-VM (QEMU/KVM) |
| `docs/` | Installations- und Härtungsanleitung |
| `spec.md` | Lebende Spezifikation mit Changelog |

## Entwicklung

```bash
dev/devserver.sh                 # lokaler Server auf 127.0.0.1:8000 (Testaccount wird angelegt)

# Tests (venv mit server/requirements.txt + pytest httpx dbus-next websockets)
cd server && python -m pytest -q tests
cd agent  && python -m pytest -q tests
cd tray   && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```

Systemnahe Teile wie PAM, nftables und Mounts **nie auf dem Entwicklungsrechner** scharf schalten,
sondern in der Test-VM ausprobieren: [`dev/vm/README.md`](dev/vm/README.md). Änderungen werden
zuerst in `spec.md` beschrieben und im Changelog datiert. Konventionen stehen in
[`CLAUDE.md`](CLAUDE.md).

## Lizenz

[PolyForm Noncommercial 1.0.0](LICENSE): Nutzung, Änderung und Weitergabe sind für
**nicht-kommerzielle** Zwecke frei, privat ebenso wie durch Schulen, Vereine oder Behörden.
Kommerzielle Nutzung ist nicht erlaubt. Bei jeder Weitergabe müssen die Lizenz und der Hinweis
`Required Notice` mit dem ursprünglichen Autor erhalten bleiben.

Weil kommerzielle Nutzung ausgeschlossen ist, ist KidCTL im Sinne der OSI „source-available“ und
nicht „Open Source“.

Copyright © 2026 Adrian Noss
