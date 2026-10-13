# Parental Control für Arch Linux – Spezifikation

Status: Anforderungen per /grill-me abgestimmt (2026-10-13). Server-Grundgerüst und Bildschirmzeit-Konfiguration umgesetzt, Client noch nicht.
Arbeitstitel: `kidctl` (Name noch nicht final). Deploy unter `kids.nossco.de` (Annahme).

## Hintergrund

Eigene Parental-Control-Software für Arch-Linux-Rechner der Kinder. Server-Client-Modell:
zentraler Server mit Eltern-WebUI (Browser + Mobil als PWA), Root-Agent auf jedem Kinderrechner.
Funktionen: Bildschirmzeit, Programm-Whitelist, Website-Filter, Messenger Eltern ↔ Kind,
Verlängerungsanfragen.

## Threat Model

- Kinder haben Standard-Accounts ohne sudo/Root.
- Abgewehrt werden naheliegende Umgehungen: anderer Browser, Programm umbenennen, eigene
  Binaries/AppImages im Home, WLAN abschalten, TTY-Login, Tray-App beenden.
- Nicht im Scope: physischer Zugriff (Live-USB, GRUB-Parameter, BIOS). Wird nur als
  Härtungs-Anleitung dokumentiert (BIOS-/Bootloader-Passwort, ggf. Secure Boot + Verschlüsselung).
- Nicht im Scope: aktive VPN-/DoH-Erkennung über das unten Beschriebene hinaus.
- Durchsetzung ausschließlich auf Systemebene durch den Root-Agenten.

## Architektur

### Server
- Python/FastAPI, SQLite, Jinja2 (serverseitig gerendert), etwas JavaScript für Live-Chat/Push.
- Docker Compose hinter dem bestehenden Traefik, öffentlich per HTTPS.
- PWA: Web-App-Manifest + Service Worker, Web Push (z. B. `pywebpush`, VAPID).
- Leichtgewicht-Migrationsskript `app/migrate.py` nach bekanntem Muster
  (Spalte prüfen → `ALTER TABLE` → verifizieren → sonst `RuntimeError`).
- `bcrypt==4.0.1` gepinnt (passlib-Inkompatibilität mit bcrypt 5.x).
- Daten als Bind-Mount (`./data:/data`), Backup extern.

### Client
- **Root-Agent** (systemd-Dienst, Python/asyncio):
  - Einziger Inhaber von Geräte-Token (`root:root 0600`) und Serververbindung.
  - Dauerhafte WebSocket-Verbindung zum Server (ausgehend), Reconnect mit Backoff.
  - Lokaler Policy-Cache, Durchsetzung auch offline.
  - Zeitzählung, Prozessüberwachung, DNS-Filter, PAM-Login-Sperre, Integritätsprüfung.
- **Tray-App** (PySide6, Autostart im Kinder-Account):
  - Restzeit-Anzeige, Chat, „Verlängerung anfordern“.
  - Spricht nur lokal mit dem Agenten über Unix-Socket; Agent prüft UID per `SO_PEERCRED`.
  - Rein kosmetisch: Beenden der Tray-App hat keinen Einfluss auf Zählung/Durchsetzung.
  - Gehört zur festen Basis-Whitelist.
- Unterstützte Umgebung: **ausschließlich KDE Plasma auf Wayland**.

### Offline-Verhalten
- Letzte bekannte Policy wird weiter durchgesetzt.
- Nutzungszeit wird lokal gezählt und beim nächsten Kontakt synchronisiert.
- Nachrichten und Verlängerungsanfragen werden in eine Warteschlange gestellt.
- Offline sind keine neuen Genehmigungen möglich.

## Datenmodell (Kern)

- **Elternteil** (Account) – mehrere, gleichberechtigt.
- **Kind** – zentrale Einheit, trägt alle Policies.
- **Gerät** – registrierter Rechner mit Geräte-Token.
- **Zuordnung** – (Gerät, Linux-Username) → Kind. Ein Kind kann mehrere Zuordnungen haben.
- **Grundregel:** Nicht zugeordnete Linux-User sind nicht eingeschränkt.
- **Tagesausnahme** – Kind + Datum + `extra_minutes` und/oder `window_end_override`.
- **Nachricht** – Chat-Eintrag; Verlängerungsanfrage ist ein spezieller Nachrichtentyp.

## Bildschirmzeit

### Regeln
- Pro Kind eine Wochenvorlage: je Wochentag **Kontingent** + **erlaubtes Zeitfenster**.
- UI-Schalter „Werktage/Wochenende gleich“.
  - An: zwei Zeilen (Mo–Fr, Sa–So), Speichern schreibt die Werte auf alle Tage der Gruppe.
  - Umschalten ändert nur die Darstellung, nicht die gespeicherten Regeln; weichen Tage einer
    Gruppe voneinander ab, zeigt das WebUI einen Hinweis (angezeigt: Werte von Mo bzw. Sa).
- Zeitfenster innerhalb eines Kalendertags (Beginn < Ende), kein Fenster über Mitternacht.
- Nutzung endet, wenn Kontingent aufgebraucht **oder** Zeitfenster geschlossen ist.
- Tageswechsel um 00:00 Europe/Berlin.

### Zählung
- Gezählt wird nur eine aktive, entsperrte Session: logind `Active=yes` und `LockedHint=no`.
- Keine Leerlauferkennung.
- Messung der Dauer über monotone Uhr (`CLOCK_BOOTTIME`); Systemuhr nur für Tag/Zeitfenster.
- Geräteübergreifend pro Kind summiert. Überziehung durch gleichzeitige Offline-Nutzung auf
  mehreren Geräten wird im WebUI nur angezeigt, nicht automatisch verrechnet.

### Ablauf bei Zeitende
- Vorwarnungen bei 15, 5 und 1 Minute(n) als KDE-Benachrichtigung mit Knopf
  „Verlängerung anfordern“.
- Nach Ablauf 60 Sekunden Countdown zum Speichern, dann `loginctl terminate-session`.
- Login-Sperre per PAM (gilt für SDDM und TTY), solange kein Kontingent/Zeitfenster verfügbar.
- SDDM zeigt Hinweis „Bildschirmzeit für heute aufgebraucht“.

### Verlängerungen
- Kind fordert an: mehr Minuten und/oder späteres Fensterende, mit Begründung (Freitext).
- Eltern: genehmigen, vor Genehmigung anpassen oder ablehnen.
- Eltern können ohne Anfrage direkt Zeit gutschreiben.
  - Nur für heute oder künftige Tage. Minuten werden zu einer bestehenden Ausnahme addiert,
    ein neues Fensterende ersetzt das bisherige und muss nach dem regulären Ende liegen.
  - Ausnahmen sind im WebUI einzeln entfernbar.
- Technisch immer eine **Tagesausnahme**, gültig nur für das jeweilige Datum.

## Programm-Whitelist

- Agent abonniert Prozess-Events über Netlink (`PROC_EVENTS`), löst `/proc/<pid>/exe` auf und
  beendet nicht erlaubte Prozesse von Kinder-UIDs.
- `/home` und `/tmp` mit `noexec` gemountet.
- Whitelist besteht aus:
  1. **Fester Basisliste** (für Eltern unsichtbar): KDE/Plasma-Komponenten (`kwin_wayland`,
     `plasmashell`, `dbus`, `pipewire`, `xdg-desktop-portal*` usw.) und Tray-App.
  2. **Eltern-Liste**: Auswahl aus den vom Client gemeldeten `.desktop`-Einträgen
     (Name, Icon, `Exec`-Pfad).
- Shells, Terminals und Interpreter (Konsole, bash interaktiv, python …) standardmäßig gesperrt.
- Browser: nur Firefox/Chromium mit gesetzter Policy sollten freigegeben werden.

### Programmquellen
- **Pacman/AUR:** Erkennung über Binary-Pfad.
- **Flatpak (nur systemweit):** Erkennung über App-ID (Cgroup `app-flatpak-<appid>-*.scope`
  bzw. `/proc/<pid>/root/.flatpak-info`). `flatpak --user` wird nicht unterstützt.
- **Steam:** nur als Ganzes freigegeben/gesperrt.
  - Bibliothek unter `/srv/steam/<user>` (ohne `noexec`), per Bind-Mount nach
    `~/.local/share/Steam`.
  - Prozesse unter diesem Präfix nur erlaubt, wenn Steam freigegeben ist **und** der Prozess
    vom Steam-Hauptprozess abstammt (Elternkette über `/proc/<pid>/stat`).
  - Bekannte Restlücke: „Nicht-Steam-Spiel hinzufügen“ in der Steam-Oberfläche.

## Website-Filter

- Modus pro Kind:
  - **Whitelist:** nur freigegebene Domains; vordefinierte **Dienst-Bundles**
    (z. B. YouTube inkl. `googlevideo.com`, `ytimg.com`, `ggpht.com`; Wikipedia; Schulplattform).
  - **Blacklist:** alles erlaubt außer gesperrten Domains; zusätzlich **auswählbare bekannte
    Sperrlisten** (z. B. OISD, StevenBlack-Varianten, HaGeZi). Server ruft die Listen regelmäßig
    ab und verteilt sie an die Clients.
- Durchsetzung auf dem Client:
  - Lokaler DNS-Resolver (z. B. Blocky) mit eigener Instanz/eigenem Port pro Kind.
  - nftables leitet DNS (Port 53) von Kinder-UIDs per `meta skuid` auf den passenden Port um.
  - DNS-over-TLS (Port 853) für Kinder-UIDs gesperrt.
  - DNS-over-HTTPS in Firefox (`policies.json`) und Chromium per Policy deaktiviert.
- Später optional: IP-Bindung im Whitelist-Modus (nftables-Set aus Resolver-Antworten).

## Messenger

- Zweiseitiger Text-Chat Eltern ↔ Kind.
- Kind: Tray-App mit Chatverlauf.
- Eltern: WebUI, Absender im Verlauf sichtbar.
- Verlängerungsanfragen erscheinen im Chat als spezielle Nachricht mit
  Genehmigen / Anpassen / Ablehnen.
- Web Push an alle Push-Abos der Eltern bei neuer Nachricht/Anfrage.
- Keine Bilder/Dateien, keine Lesebestätigungen.

## Eltern-Accounts & Sicherheit

- Mehrere gleichberechtigte Eltern-Accounts; jeder sieht und verwaltet alle Kinder.
- Bootstrap des ersten Accounts per CLI-Skript, weitere im WebUI.
- TOTP-2FA verpflichtend (`pyotp`).
- Login-Rate-Limiting (z. B. 5 Fehlversuche / 15 min pro IP und Account).
- Sessions 30 Tage gültig.
- CSRF-Schutz auf allen Formularen/Aktionen.
- Push-Abos pro Account und Endgerät.

## Geräteverwaltung

- „Gerät hinzufügen“ im WebUI erzeugt Kopplungscode (8 Zeichen, 10 min gültig, einmalig).
- Auf dem Client: `sudo kidctl enroll <server-url> <code>` → Tausch gegen langlebiges Geräte-Token.
- Agent meldet lokale Benutzer (UID ≥ 1000) und installierte Apps.
- Zuordnung Linux-User → Kind im WebUI.
- Geräte-Token im WebUI widerrufbar.

## Auswertung & Alarme

- Status: aktuelle Restzeit pro Kind, Online-Status pro Gerät.
- Nutzungsstatistik pro Tag/Woche (Diagramm) und Zeit pro Programm.
- Protokoll **blockierter** Aktionen (gesperrtes Programm, gesperrte Domain).
- **Kein** Verlauf erlaubter Websites.
- Push-Alarme:
  - Gerät hat sich länger als X Stunden nicht gemeldet (Standard 48 h, pro Gerät einstellbar).
  - Integritätsfehler des Agenten (z. B. nftables-Regeln fehlen, PAM-Konfiguration verändert).

## Verteilung & Updates

- Client als PKGBUILD: Agent, Tray-App, systemd-Unit, PAM-Konfiguration, nftables-Regeln,
  Firefox/Chromium-Policies, Mount-Konfiguration (`noexec`, Steam-Bind-Mount).
- Eigenes, GPG-signiertes Pacman-Repository als statisches Verzeichnis im Docker/Traefik-Stack.
- Updates über normales `pacman -Syu`.
- Protokollversion im WebSocket-Handshake; Server unterstützt mindestens eine ältere Version.
  Zu alter Agent → Hinweis im WebUI, Durchsetzung läuft mit letzter Policy weiter.

## Bewusst nicht umgesetzt / Restlücken

- Kein Schutz gegen physischen Zugriff (nur Härtungs-Anleitung).
- Keine Leerlauferkennung.
- Keine Unterstützung anderer Desktops als KDE Plasma (Wayland).
- Kein `flatpak --user`, keine Pro-Spiel-Steuerung bei Steam.
- „Nicht-Steam-Spiel hinzufügen“ als bekannte Restlücke.
- Keine IP-Bindung im ersten Release.
- Kein Browserverlauf, keine Bilder/Dateien im Chat, keine Lesebestätigungen.
- Kein Selbst-Update des Agenten (nur über Pacman).

## Offene Punkte

- Finaler Projektname (Arbeitstitel `kidctl`) und Subdomain (Annahme `kids.nossco.de`).
- Inhalt der Dienst-Bundles und Vorauswahl der Sperrlisten.

## Changelog

- 2026-10-13: Anforderungen per /grill-me abgestimmt, Spezifikation erstellt. Noch keine Umsetzung.
- 2026-10-13: Server-Grundgerüst umgesetzt (`server/`, `common/`): Datenmodell (Eltern, Sessions,
  Push-Abos, Kinder mit Wochenvorlage, Tagesausnahmen, Geräte, User-Zuordnung, Kopplungscodes,
  Nachrichten), Eltern-Login mit Passwort + TOTP (Replay-Schutz), Rate-Limit 5/15 min pro IP und
  Account, CSRF (Session-Token bzw. Double-Submit-Cookie vor dem Login) + Origin-Prüfung,
  30-Tage-Sessions, Bootstrap-CLI `python -m app.cli create-parent`, Eltern-Verwaltung im WebUI
  (TOTP-QR einmalig), Kinder anlegen/löschen, Geräte koppeln (8-Zeichen-Code, 10 min, einmalig,
  nur Hash gespeichert), Benutzer-Zuordnung, Token-Widerruf (trennt laufende Verbindung),
  WebSocket `/api/v1/ws` mit Protokoll v1 (`hello`/`welcome`, `ping`/`pong`, `report_users`),
  veraltete Agenten werden im WebUI markiert. Dockerfile + Compose mit Traefik-Labels.
  Getestet: 16 pytest-Tests (Login/TOTP/Replay, Rate-Limits Login + Kopplung, CSRF/Origin,
  Kopplung → WebSocket → User-Bericht → Zuordnung → Widerruf, alte Protokollversion, Migration
  gegen synthetisches Alt-Schema inkl. Hart-Abbruch) sowie Live-Smoke-Test mit uvicorn,
  CLI-Bootstrap, echtem HTTP-Login und `websockets`-Client. Docker-Build nicht getestet
  (kein Docker auf dem Entwicklungssystem).
- 2026-10-13: Bildschirmzeit-Konfiguration im WebUI (`/children/<id>`): Wochenvorlage mit Schalter
  „Werktage/Wochenende gleich“, direkte Gutschrift als Tagesausnahme (Minuten und/oder späteres
  Fensterende), Liste heutiger/künftiger Ausnahmen mit Entfernen. Policy-Verteilung an Agenten
  folgt später. Getestet: 6 neue pytest-Tests (Gruppenmodus, Einzeltage + Abweichungs-Hinweis,
  Validierung ohne Teilspeicherung, Gutschrift addieren/ersetzen/entfernen, Validierung der
  Gutschrift, CSRF) – gesamt 22 grün; Live-Smoke-Test mit uvicorn (Vorlage speichern,
  Gutschrift, DB-Inhalt geprüft).
