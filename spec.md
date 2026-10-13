# Parental Control für Arch Linux – Spezifikation

Status: Anforderungen per /grill-me abgestimmt (2026-10-13). Serverseite für Bildschirmzeit, Chat/Verlängerungen, PWA/Web Push, Policy-Verteilung und Offline-Alarm umgesetzt; Agent Bildschirmzeit inkl. PAM-Login-Sperre umgesetzt.
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
- PWA: Web-App-Manifest + Service Worker, Web Push (`pywebpush`, VAPID).
  - VAPID-Schlüssel aus `.env` (`VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`),
    erzeugt per `python -m app.cli vapid-keys`. Ohne Schlüssel ist Push deaktiviert (Hinweis im WebUI).
  - Service Worker ohne Offline-Cache (nur Push + Klick öffnet die passende Seite).
  - Push pro Gerät/Browser auf der Seite „Benachrichtigungen“ aktivierbar, mit Test-Knopf.
  - Abgelaufene Abos (HTTP 404/410 vom Push-Dienst) werden automatisch entfernt.
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
  Nur Sitzungen der Klasse `user` (SDDM, TTY, SSH); Greeter, `manager` und `background`
  werden weder gezählt noch beendet. Je User wird höchstens einmal pro Takt gezählt.
- Die 60 s Countdown nach Zeitende zählen mit (Überziehung um bis zu 1 min möglich).
- Keine Leerlauferkennung.
- Messung der Dauer über monotone Uhr (`CLOCK_BOOTTIME`); Systemuhr nur für Tag/Zeitfenster.
- Geräteübergreifend pro Kind summiert. Überziehung durch gleichzeitige Offline-Nutzung auf
  mehreren Geräten wird im WebUI nur angezeigt, nicht automatisch verrechnet.
- Synchronisation: Agent meldet je (Linux-User, Tag) seinen **absoluten** lokalen Zählerstand
  (`usage_report`, idempotent, Server übernimmt das Maximum). Server sendet je Kind die Summe
  der **anderen** Geräte (`usage_update`); effektive Nutzung = eigene Zählung + andere Geräte.
  Nicht zugeordnete User und Meldungen außerhalb von heute−7 … heute+1 werden ignoriert.

### Policy-Verteilung
- Server sendet nach dem Handshake und nach jeder relevanten Änderung (Wochenvorlage,
  Gutschrift, Genehmigung, Ausnahme entfernt, Zuordnung, Kind gelöscht) die vollständige Policy
  des Geräts: Zeitzone, Zuordnung User → Kind (nur zugeordnete User), Wochenregeln und
  Tagesausnahmen ab gestern.
- `version` = Hash des Inhalts; Agent bestätigt per `policy_ack`, WebUI zeigt je Gerät
  „aktuell / veraltet / noch nicht übertragen“.

### Ablauf bei Zeitende
- Vorwarnungen bei 15, 5 und 1 Minute(n) als KDE-Benachrichtigung mit Knopf
  „Verlängerung anfordern“.
- Nach Ablauf 60 Sekunden Countdown zum Speichern, dann Abmeldung:
  - Grafische Sitzungen (Wayland/X11) über Plasma (`org.kde.Shutdown.logout`, ohne Rückfrage).
    `TerminateSession` würde den Sitzungsleiter `sddm-helper` mitbeenden – SDDM wertet das als
    Absturz und zeigt keinen Anmeldebildschirm mehr (im VM-Test beobachtet).
  - Wirkt die Plasma-Abmeldung nach 20 s nicht: hart per `TerminateSession`; zeigt seat0 danach
    weder Greeter noch andere Sitzung, wird `display-manager.service` neu gestartet.
  - TTY-/SSH-Sitzungen direkt per `TerminateSession`.
- Login-Sperre per PAM (gilt für SDDM und TTY), solange kein Kontingent/Zeitfenster verfügbar.
  - Agent schreibt alle 5 s `/run/kidctl/locked-users` (zugeordnete User ohne Zeit, root 0644)
    und `/run/kidctl/locked-message`; sofort beim Start aus der gecachten Policy.
  - PAM: markierter Block am Ende des Account-Teils von `/etc/pam.d/system-login`
    (`pam_listfile` sense=deny `onerr=succeed`, `pam_echo` Hinweis, `pam_deny`). Greift für SDDM,
    SDDM-Autologin, TTY (`login`) und SSH; nicht für Bildschirmsperre (`kde`) und `systemd-user`.
  - Ausfallsicher: fehlt/unlesbar die Sperrdatei, ist die Anmeldung erlaubt. Kein Python im
    Anmeldepfad.
  - Ein-/Austragen per `packaging/pam/kidctl-pam.sh install|remove [--apply]` (Dry-Run-Standard,
    Sicherung `system-login.kidctl.bak`, idempotent).
- Admin-Schutz: root und Mitglieder von `wheel`/`sudo`/`admin` werden nie eingeschränkt, auch wenn
  sie einem Kind zugeordnet sind (Warnung im Agent-Log).
- SDDM zeigt Hinweis „Bildschirmzeit für heute aufgebraucht“.
  - Breeze wertet PAM-Hinweise nicht aus (zeigt nur „Login Failed“). Daher eigenes SDDM-Theme
    `kidctl`, das Breeze unverändert per `Loader` lädt und bei abgelehnter Anmeldung die PAM-
    Hinweise (alle Meldungen außer dem abschließenden PAM-Fehlertext) 10 s lang anzeigt.
    Falsches Passwort → weiterhin „Login Failed“. `theme.conf` ist ein Symlink auf Breeze.
  - Aktivierung über `/etc/sddm.conf.d/zz-kidctl-theme.conf` (nach `kde_settings.conf` gelesen);
    Installation per `packaging/sddm/kidctl-sddm.sh install|remove [--apply]`.

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
- Zustellung: Agent leitet Nachrichten der Tray-App per WebSocket weiter (`chat_send`,
  `extension_request`) und puffert sie offline; `client_msg_id` macht das Senden idempotent,
  der Server bestätigt mit `ack`. Server stellt neue/geänderte Einträge als `chat_message` an
  alle online-Geräte des Kindes zu (Upsert per `id`); Verlauf per `chat_sync` nachholbar.
- Eltern-WebUI aktualisiert den Chat per Polling (5 s).
- Verlängerungsanfragen für vergangene Tage verfallen automatisch (Status `expired`).
- Gleichzeitige Entscheidungen zweier Eltern: nur die erste wird übernommen.

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
    Prüfung alle 5 min, ein Alarm pro Offline-Phase (erneut erst nach erneuter Meldung).
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
- 2026-10-13: Chat & Verlängerungsanfragen (Serverseite): Protokoll v1 (noch unveröffentlicht)
  um `chat_send`, `extension_request`, `chat_sync`, `ack`, `chat_message` erweitert; Chat-Seite
  pro Kind mit Live-Aktualisierung, Genehmigen / Anpassen / Ablehnen (Genehmigung erzeugt bzw.
  erweitert die Tagesausnahme), offene Anfragen in Übersicht und Kinderliste. Migration
  `message`: `approved_minutes`, `approved_window_end`, `device_id`, `client_msg_id`.
  Web Push folgt separat. Getestet: 6 neue pytest-Tests (Ack/Duplikat/nicht zugeordneter User,
  Eltern-Nachricht zugestellt, angepasste Genehmigung inkl. Tagesausnahme + Doppelentscheidung,
  Ablehnung + ungültige Genehmigung, Verfall + `chat_sync`, Migration gegen Alt-Schema) – gesamt
  28 grün; Live-Smoke-Test mit uvicorn gegen bestehende Alt-Datenbank (Migration im Log,
  Anfrage per `websockets`-Client, Genehmigung im WebUI, Entscheidung beim Agenten empfangen).
- 2026-10-13: PWA & Web Push: Manifest, Icons, Service Worker (`/sw.js`), Seite „Benachrichtigungen“
  (Abo pro Gerät, Test-Push), Push an alle Eltern bei neuer Kind-Nachricht/Verlängerungsanfrage
  (nicht blockierend im Thread-Pool), CLI `vapid-keys`, CSRF zusätzlich per Header
  `X-CSRF-Token` für JSON-Aufrufe. Alarme (Gerät offline, Integrität) folgen. Getestet: 5 neue
  pytest-Tests (PWA-Dateien, Abo inkl. CSRF/HTTPS-Pflicht/Idempotenz/Abmelden, Test-Push mit
  echter aes128gcm-Verschlüsselung + VAPID-Header und Entschlüsselung, Push bei Kind-Anfrage,
  Entfernen bei 410) – gesamt 33 grün; Live-Smoke-Test mit uvicorn (Seiten, Abo, Push an
  unerreichbaren Endpunkt blockiert Chat nicht, Abo bleibt). **Nicht getestet:** Zustellung
  über einen echten Push-Dienst an einen echten Browser/ein Handy (nach Deployment prüfen).
- 2026-10-13: Policy-Verteilung & Nutzungszeit: Protokoll v1 um `policy`, `policy_ack`,
  `usage_report`, `usage_update` erweitert; Push der Policy bei allen Regeländerungen;
  geräteübergreifende Summe; Übersicht zeigt je Kind „heute genutzt / erlaubt / Rest“ und
  Überziehung; Geräteseite zeigt Policy-Stand. Offline-Alarm als Hintergrundaufgabe mit Web Push.
  Migration `device`: `policy_version`, `offline_alerted_at`; neue Tabelle `usage_record`.
  Getestet: 8 neue pytest-Tests (Policy bei Verbindung/Zuordnung/Ack/Regeländerung,
  Gutschrift + Ausnahme entfernen, Kind löschen hebt Einschränkung auf, Summe über zwei Geräte
  inkl. Maximum-Regel/ignorierte Einträge/Überziehungsanzeige, Nachholen nach Reconnect,
  Offline-Alarm einmal pro Phase, kein Alarm für verbundene/widerrufene Geräte, Migration) –
  gesamt 41 grün; Live-Smoke-Test mit uvicorn gegen Alt-Datenbank (Migration im Log) und zwei
  `websockets`-Agenten (Policy, Ack, `usage_update` an das andere Gerät, Gutschrift an beide,
  Übersicht).
- 2026-10-13: Test-VM vorbereitet (`dev/vm/`): Arch-Cloud-Image (SHA256 geprüft) + Overlay-Disk,
  cloud-init mit Benutzern `admin` (sudo, SSH-Schlüssel) und `kind`, Provisionierung (Plasma
  Wayland, SDDM unter Wayland, deutsche Locale/Tastatur, Python-Pakete aus offiziellen Repos),
  Repo per 9p schreibgeschützt eingebunden, Snapshots mit automatischer Sicherung vor Restore.
  Alle Skripte mit Dry-Run-Standard. Getestet: Bash-Syntax, Dry-Runs, alle 27 Paketnamen gegen
  die Repos, erzeugtes cloud-init-YAML (parsebar, Platzhalter ersetzt, eingebettetes Skript
  identisch), Erreichbarkeit des Cloud-Images. **Nicht getestet:** tatsächlicher VM-Start (QEMU
  auf dem Host noch nicht installiert).
- 2026-10-13: Test-VM real aufgesetzt und gestartet: Image geladen + SHA256 ok, cloud-init legt
  `admin`/`kind` an, 9p-Mount `/opt/kidctl-src` ok, Provisionierung fertig (Plasma 6.7, PySide6,
  python-websockets, python-dbus-next, python-pydantic, Locale de_DE/Tastatur de), nach Neustart
  SDDM-Greeter unter `kwin_wayland`, keine relevanten Boot-Fehler. Screenshot per QMP mit virgl
  nicht möglich (bekannte Einschränkung) – Sichtprüfung im VM-Fenster.
- 2026-10-13: Agent Teil 1 (`agent/`): `kidctl enroll|run|status`, Zustand in `/var/lib/kidctl`
  (root, 0700/0600, atomar), WebSocket-Verbindung mit Backoff, Policy-Cache + Ack, Nutzungsbericht,
  Zeitzählung per logind/D-Bus und `CLOCK_BOOTTIME`, Vorwarnungen 15/5/1 min, 60-s-Countdown,
  `TerminateSession`. Noch offen: PAM-Login-Sperre, Tray/Chat, Aktionsknopf „Verlängerung
  anfordern“, systemd-Unit/PKGBUILD. Getestet: 16 pytest-Tests (Regeln inkl. Ausnahmen,
  Zählung inkl. Suspend-Lücken/Persistenz/Rechte, Warnstufen, Countdown/Abbruch/Beenden, nicht
  zugeordnete User unangetastet, hängendes notify-send, E2E gegen echten uvicorn-Server:
  enroll → Policy → Zuordnung → Ack → Nutzung im WebUI → Widerruf → Offline-Policy). In der
  Test-VM gegen den Entwicklungsserver: enroll (Token root:root 0600), Agent als transiente
  systemd-Unit, Policy „aktuell“ im WebUI, simulierte `user`-Sitzung von `kind` (PAM `login`) nach
  Kontingent + 60 s exakt beendet, Gutschrift wirkt sofort. Erkenntnisse: `systemd-run`-Sitzungen
  sind Klasse `background` (für Tests `XDG_SESSION_CLASS=user` setzen); `notify-send` ohne
  Benachrichtigungsdienst hängt → Timeout; `runuser` erzeugte PAM-Sitzungen → `setpriv`.
  **Nicht getestet:** sichtbare KDE-Benachrichtigungen in einer echten Plasma-Sitzung.
- 2026-10-13: Agent: nach dem Start bis zu 10 s auf die aktuelle Server-Policy warten, bevor
  durchgesetzt wird (Zählung läuft weiter). Behebt Fehlalarm „Bildschirmzeit zu Ende“ mit
  veralteter gecachter Policy, beobachtet im VM-Test mit echter Plasma-Sitzung. Ohne Server gilt
  danach die gecachte Policy. Getestet: 2 neue pytest-Tests (gesamt 18). VM-Test mit angemeldetem
  `kind` in Plasma: Start mit 3 min Restzeit → Countdown nach 3:00 min → Abmeldung nach 60 s;
  kein hängendes notify-send.
- 2026-10-13: Agent: Abmeldung grafischer Sitzungen über Plasma statt `TerminateSession`, harter
  Rückfall nach 20 s mit Wiederherstellung des Anmeldebildschirms; `notify.py` → `usersession.py`.
  Anlass: VM-Test zeigte nach `TerminateSession` nur noch blinkenden Cursor („sddm-helper crashed“).
  Getestet: 3 neue pytest-Tests (gesamt 21: Plasma-Abmeldung, harter Rückfall + DM-Neustart,
  kein DM-Neustart bei belegtem seat0). VM mit echter Plasma-Sitzung (Autologin nur für den Test,
  danach entfernt): Countdown → Plasma-Abmeldung → Greeter auf tty1, kein SDDM-Fehler. KDE-
  Benachrichtigungen (Restzeit, 1 min, Zeitende) vom Nutzer im VM-Fenster bestätigt.
- 2026-10-13: Agent Teil 2 – PAM-Login-Sperre (`agent/kidctl_agent/loginlock.py`,
  `packaging/pam/kidctl-pam.sh`), Admin-Schutz, Test-Werkzeug `dev/vm/pamtest.py`. Getestet:
  3 neue pytest-Tests (gesamt 24: Sperrdatei + Rechte, echte Admin-Erkennung, sofortiges Schreiben
  beim Start ohne Admin); Install-Skript gegen Dateikopie (Dry-Run unverändert, idempotent,
  Entfernen = Original). In der VM (Snapshot `vor-pam` vorher): ohne Block erlaubt; Block
  installiert; ohne Sperrdatei erlaubt (fail-open); mit Agent `kind` abgelehnt über `login`,
  `sddm`, `sddm-autologin`, `sshd` jeweils mit Hinweistext, `admin` erlaubt; Gutschrift im WebUI →
  nach ≤ 5 s erlaubt, Gutschrift entfernt → nach ≤ 5 s wieder gesperrt.
- 2026-10-13: SDDM-Theme `kidctl` (Breeze-Hülle) für den Hinweis der Login-Sperre. Anlass: im VM-
  Test zeigte Breeze nur „Login Failed“, erste Version zeigte den nachfolgenden PAM-Fehlertext
  („Authentication failure“), weil SDDM Hinweis und Fehlertext über denselben Kanal schickt.
  Getestet in der VM (kein QML-Fehler im Greeter-Log), vom Nutzer im VM-Fenster bestätigt:
  Aussehen unverändert, `admin` meldet sich normal an, `kind` (gesperrt) sieht den
  Bildschirmzeit-Hinweis, falsches Passwort zeigt weiterhin „Login Failed“.
