# Parental Control für Arch Linux – Spezifikation

Status: Anforderungen per /grill-me abgestimmt (2026-10-13). Serverseite für Bildschirmzeit, Chat/Verlängerungen, PWA/Web Push, Policy-Verteilung und Offline-Alarm umgesetzt; Agent (Bildschirmzeit, PAM-Login-Sperre), Tray-App und Paket umgesetzt.
Projektname: **KidCTL** (Paket, Befehle und Pfade technisch `kidctl`). Deploy unter `kids.nossco.de` (Annahme).
Lizenz: PolyForm Noncommercial 1.0.0 (`LICENSE`) – Nutzung, Änderung und Weitergabe frei, außer zu
kommerziellen Zwecken; der „Required Notice“ mit Adrian Noss als ursprünglichem Autor muss bei jeder
Weitergabe erhalten bleiben. Hinweis: Wegen des Verbots kommerzieller Nutzung ist das im Sinne der
OSI „source-available“, nicht „Open Source“.

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
  Härtungs-Anleitung dokumentiert: `docs/haertung.md` (root sperren, Firmware-/Bootloader-Passwort,
  optional Verschlüsselung, Secure Boot + TPM).
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

### Jetzt sperren (entschieden 2026-10-15)
- Eltern sperren ein **Kind** (alle zugeordneten Rechner) sofort: Knopf auf der Übersicht (bis zur
  Freigabe) und auf der Kind-Seite mit Dauer: bis zur Freigabe, 30 min, 1 h, 2 h, bis morgen (00:00 Uhr
  lokal).
- Ablauf wie bei Zeitende: Benachrichtigung „Gesperrt“ mit Vorwarnung (ohne „Verlängerung
  anfordern“), dann Abmeldung. **Vorwarnzeit** beim Sperren auf der Kind-Seite wählbar (0–120 min,
  Standard 1, 0 = sofort; entschieden 2026-10-16), Schnellknopf der Übersicht = 1 min; bei mehr als
  1 min zusätzlich Erinnerung 1 min vor der Abmeldung. Gilt nur für „Jetzt sperren“ – beim Zeitende
  bleibt der 60-s-Countdown (Warnungen 15/5/1 min gibt es dort vorher); Login-Sperre per PAM solange gesperrt. Freigabe während des Countdowns
  bricht ihn ab („Wieder freigegeben“).
- Policy-Felder `locked`/`locked_until` (UTC). Der Agent prüft die Sperre vor Kontingent/Zeitfenster;
  eine befristete Sperre läuft auch offline ab (Systemuhr). Freigeben erreicht nur verbundene Geräte.

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
- `/home` und `/tmp` mit `noexec` gemountet. Umsetzung (entschieden 2026-10-14):
  - **Kinder-Homes** (zugeordnete Nicht-Admins mit Programmkontrolle ≠ Aus) je per Bind-Mount
    auf sich selbst mit `noexec,nosuid,nodev` – Admin-/Eltern-Homes bleiben ausführbar
    (Grundregel). Der Agent legt die Mounts als transiente systemd-Units an (`systemd-mount`,
    Beschreibung „kidctl: noexec-Home“), beim Start vor den Logins und bei Policy-Änderungen;
    Programmkontrolle aus → Mount wird aufgehoben. Nur für Homes genau unter `/home/<name>`,
    kein Symlink, Eigentümer = Kind.
  - **Systemweit** `/tmp`, `/var/tmp` (Bind-Mount auf sich selbst) und `/dev/shm` mit `noexec`
    (`kidctl-noexec.service`, vom Agenten per `Wants=` mitgestartet, `ExecStop` nimmt es zurück).
  - Aufheben (Programmkontrolle aus / Zuordnung entfernt) nie während der Besitzer angemeldet ist
    (`/run/user/<uid>` existiert) – sonst bricht die laufende Plasma-Sitzung ab; der Agent holt es nach
    der Abmeldung nach.
- Whitelist besteht aus:
  1. **Fester Basisliste** (für Eltern unsichtbar): KDE/Plasma-Komponenten (`kwin_wayland`,
     `plasmashell`, `dbus`, `pipewire`, `xdg-desktop-portal*` usw.) und Tray-App.
  2. **Eltern-Liste**: Auswahl aus den vom Client gemeldeten `.desktop`-Einträgen
     (Name, Icon, `Exec`-Pfad).
- Shells, Terminals und Interpreter (Konsole, bash interaktiv, python …) standardmäßig gesperrt.
- Umsetzung (Agent):
  - Prüfung je Prozess zugeordneter Kinder: sofort per Netlink `PROC_EVENTS`, zusätzlich Scan
    aller Kinder-Prozesse alle 5 s (falls Events verloren gehen). Gesperrt = `SIGKILL`,
    KDE-Benachrichtigung „Programm gesperrt“, Eintrag im Sperrprotokoll.
  - Modus je Kind: **Aus**, **Nur protokollieren** (meldet, was gesperrt würde – gefahrloser
    Einstieg), **Sperren**. *(Ergänzung zur Spec, Bestätigung ausstehend.)*
  - Basisliste wird aus Paketen berechnet: alle ausführbaren Dateien (/usr/bin, /usr/lib) der
    Abhängigkeitskette von Plasma/KWin/Xwayland/Pipewire/Portalen/Flatpak
    (`/usr/lib/kidctl/base-programs.txt`, `pkg:`-Zeilen) sowie aller installierten Pakete der
    Gruppen `plasma` und `kf6` (`group:`-Zeilen, entschieden 2026-10-16: KDE-Hintergrunddienste wie
    kded, kwalletd, powerdevil, baloo, kactivitymanagerd mussten sonst einzeln freigegeben werden;
    `socat`/`filan`/`procan` ausgeschlossen), ohne Programme mit sichtbarem
    .desktop-Eintrag (= Apps, Eltern-Entscheidung), ohne Shells/Interpreter/Terminals und ohne
    dynamischen Loader (`ld-linux*`, startet sonst beliebige Programme). Neu berechnet beim Start
    und nach pacman-Updates. Ist sie leer (Fehler), wird nur protokolliert – nie die Sitzung zerstört.
  - Eltern-Liste: App-Schlüssel `exe:<pfad>` bzw. `flatpak:<app-id>` aus dem gemeldeten Katalog
    (systemweite .desktop-Einträge; nicht `~/.local/share/applications`). Hilfsprogramme im
    Verzeichnis einer freigegebenen App (z. B. `/usr/lib/firefox/*`) sind mit erlaubt.
  - Shells nur ohne Steuerterminal (Skripte von Systemkomponenten); Interpreter nur mit
    freigegebener Skriptdatei (kein `-c`/`-m`/`-e`, stdin, REPL). kidctl-Wrapper starten daher
    Skriptdateien unter `/usr/lib/kidctl/`.
- Server: Katalog je Gerät, Freigaben je Kind (`child_program`), Sperrprotokoll (`blocked_event`),
  WebUI-Abschnitt „Programme“ und „Gesperrte Aktionen“ auf der Kind-Seite.
- Browser: nur Firefox/Chromium mit gesetzter Policy sollten freigegeben werden.

### Programmquellen
- **Pacman/AUR:** Erkennung über Binary-Pfad.
- **Flatpak (nur systemweit):** Erkennung über App-ID (Cgroup `app-flatpak-<appid>-*.scope`
  bzw. `/proc/<pid>/root/.flatpak-info`). `flatpak --user` wird nicht unterstützt.
- **Steam:** nur als Ganzes freigegeben/gesperrt.
  - Bibliothek unter `/srv/steam/<user>` (ohne `noexec`, Eigentümer Kind, 0700).
    `~/.local/share/Steam` ist ein **Symlink** dorthin statt eines Bind-Mounts (entschieden
    2026-10-14): Ein Mount-Ziel im Kinder-Home ließe sich per Symlink auf Systempfade umlenken
    (Weg zu Root-Rechten). Den Link legt der Agent mit den Rechten des Kindes an; ein vorhandenes
    echtes `~/.local/share/Steam` wird nicht angefasst (Warnung im Log).
  - Prozesse unter diesem Präfix nur erlaubt, wenn Steam freigegeben ist **und** der Prozess
    vom Steam-Hauptprozess abstammt (Elternkette über `/proc/<pid>/stat`). Die Einstiegspunkte
    `steam.sh` und `ubuntu12_32/steam` selbst sind bei Freigabe erlaubt (der Starter ruft
    `steam.sh` per exec auf). Freigabe = Steam-Starter (`/usr/lib/steam/…`) in der Eltern-Liste.
  - Bei Freigabe sind auch Nachfahren von Steam außerhalb der Bibliothek erlaubt
    (Laufzeit-Container pressure-vessel, Proton).
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
- Umsetzung (2026-10-14):
  - Modus je Kind zusätzlich **Aus** (Standard; bestehende Kinder bleiben aus, bis Eltern
    konfigurieren). Neue Spalte `child.web_mode` statt der nie genutzten `web_filter_mode`.
  - Resolver: **Unbound** statt Blocky (Blocky gibt es nur im AUR; Unbound aus `extra`):
    `kidctl-dns@<kind>.service` (DynamicUser, gehärtet), Port 5300 + Kind-ID, Konfiguration vom
    Agenten unter `/run/kidctl/dns`. Whitelist: `local-zone "." always_nxdomain` + freigegebene
    Domains `transparent` (inkl. Subdomains; CNAME-Ziele löst Unbound mit auf). Blacklist:
    Listen/eigene Sperren `always_nxdomain`, eigene Freigaben als Ausnahmen. Rangfolge ohne
    Doppelte: DoH-Anbieter > SafeSearch > Freigaben > Sperren/Listen.
  - nftables-Tabelle `inet kidctl_dns`: Port 53 (TCP/UDP) der Kinder-UIDs → Resolver des Kindes,
    Port 853 (TCP/UDP) gesperrt. Admins/nicht zugeordnete User unberührt.
  - `nss-resolve` aus `/etc/nsswitch.conf` entfernt (`kidctl-nsswitch.sh`, Dry-Run/.bak, beim
    Entfernen wiederhergestellt): sonst lösen glibc-Programme (z. B. Firefox) über den
    resolved-Socket statt Port 53 auf und umgehen die Umleitung.
  - Bekannte DoH-Anbieter immer gesperrt, inkl. `use-application-dns.net` (schaltet Firefox-Auto-
    DoH ab); Browser-Policies: Firefox `DNSOverHTTPS` aus/gesperrt, Chromium `DnsOverHttpsMode=off`.
  - **Dienst-Bundles** (entschieden 2026-10-14): YouTube, Wikipedia, ANTON, Scratch, Khan Academy,
    Microsoft 365/Teams. Schulplattform: Eltern tragen die Domain selbst ein.
  - **Sperrlisten**: HaGeZi Multi Normal/Pro/NSFW/Glücksspiel, OISD big/NSFW, StevenBlack
    Unified/Porn/Glücksspiel; Vorauswahl **HaGeZi Multi Normal + NSFW** (entschieden 2026-10-14).
    Server lädt benutzte Listen täglich (und sofort bei erster Auswahl), normalisiert sie (Domains,
    hosts, Adblock) und verteilt sie unter `/api/v1/lists/<id>` (Geräte-Token, ETag/SHA-256); der
    Agent prüft die Prüfsumme und cacht sie (offline nutzbar).
  - **SafeSearch** je Kind, Standard an (Ergänzung, entschieden 2026-10-14): Google, Bing,
    DuckDuckGo per CNAME, YouTube „eingeschränkt (moderat)“. Im Whitelist-Modus nur für
    freigegebene Domains (keine Hintertür).
  - Ressourcen: Vorauswahl ≈ 250 000 Domains → ≈ 110 MB RAM je Kind, Start < 1 s.

## Messenger

- Zweiseitiger Text-Chat Eltern ↔ Kind.
- Kind: Tray-App mit Chatverlauf.
- Eltern: WebUI, Absender im Verlauf sichtbar.
- Verlängerungsanfragen erscheinen im Chat als spezielle Nachricht mit
  Genehmigen / Anpassen / Ablehnen.
- Web Push an alle Push-Abos der Eltern bei neuer Nachricht/Anfrage.
- Keine Bilder/Dateien, keine Lesebestätigungen.
- Tray ↔ Agent: Unix-Socket `/run/kidctl/tray.sock` (0666), Identität ausschließlich per
  `SO_PEERCRED`; nicht zugeordnete User/Admins erhalten nur „nicht eingeschränkt“. Agent hält
  Chat-Verlauf (200 Nachrichten je Kind) und Ausgangs-Warteschlange persistent (offline-fähig,
  Versand nach Wiederverbindung, Entfernen erst nach `ack`), holt Verlauf nach jeder Policy per
  `chat_sync` nach. KDE-Benachrichtigung bei neuer Eltern-Nachricht und bei Entscheidung über
  eine Anfrage (nicht beim allerersten Abgleich).
- Vorwarnungen und Zeitende-Meldung haben den Knopf „Verlängerung anfordern“
  (`notify-send --action … --wait`); Klick öffnet den Anfrage-Dialog der Tray-App.
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

## Sicherheit Agent ↔ Server

- Transport nur TLS: `kidctl enroll` verlangt `https://` (`--insecure-http` nur für Test-Server,
  wird in `device.json` vermerkt); der Agent baut ohne diesen Vermerk keine `ws://`-Verbindung auf.
  Zertifikatsprüfung über die System-CAs (websockets/urllib, Standard).
- Kopplung: 8-Zeichen-Code (≈ 40 Bit), 10 min, einmalig, Rate-Limit je IP; danach 256-Bit-Geräte-Token,
  serverseitig nur als HMAC(SECRET_KEY) gespeichert, auf dem Client `root:root 0600`. Widerruf
  wirkt sofort (Prüfung bei jeder Nachricht).
- Server ordnet alle Agent-Meldungen (Chat, Nutzung, Programme) nur den Benutzern **dieses**
  Geräts zu; ein Gerät kann keine Daten anderer Geräte/Kinder lesen oder schreiben.
- Grundsatz: Der Server steuert nur die Policy, nie das System. Werte, die der Agent in Dateien,
  Pfade oder Konfigurationen schreibt, sind streng geprüft:
  - Benutzernamen `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$?$` (kein Zeilenumbruch → keine zusätzlichen
    Einträge in der PAM-Sperrliste); Sperrliste zusätzlich ohne Admins.
  - Sperrlisten-IDs `^[a-z0-9][a-z0-9-]{0,63}$` (Dateiname im Cache, kein Pfad).
  - Kind-IDs 1–60000 (Resolver-Port 5300 + ID).
  - Domains (Freigaben, Sperren, Sperrlisten) nur als gültige Hostnamen in die Unbound-Konfiguration.
  - Sperrlisten-Download folgt keinen Weiterleitungen (Token nur an den eigenen Server).
  - Texte (Chat, Benachrichtigungen) nur als Daten: Tray maskiert HTML, `notify-send` mit `--`.
- Restrisiko: Wer den Server oder einen Eltern-Account übernimmt, kann die Einschränkungen der
  Kinder ändern (lockern/verschärfen) – aber keine Admins sperren, keine Programme als root starten
  und keine Systemdateien verändern.

## Geräteverwaltung

- „Gerät hinzufügen“ im WebUI erzeugt Kopplungscode (8 Zeichen, 10 min gültig, einmalig).
- Auf dem Client: `sudo kidctl enroll <server-url> <code>` → Tausch gegen langlebiges Geräte-Token.
- Agent meldet lokale Benutzer (UID ≥ 1000) und installierte Apps.
- Zuordnung Linux-User → Kind im WebUI.
- Geräte-Token im WebUI widerrufbar.

## Auswertung & Alarme

- Status: aktuelle Restzeit pro Kind, Online-Status pro Gerät.
- Nutzungsstatistik pro Tag/Woche (Diagramm) und Zeit pro Programm.
  - Seite „Statistik“ je Kind: Kacheln (diese Woche, letzte Woche, Ø pro Tag), Säulen der letzten
    14 Tage mit Markierung des erlaubten Kontingents (Tooltip, Tabellenansicht), Programmzeit als
    Balken (heute / letzte 7 Tage). Reine CSS-Diagramme ohne JavaScript.
  - **Zeit pro Programm = „geöffnet“** (entschieden 2026-10-14): Zeit, in der eine App aus dem
    Katalog während gezählter Bildschirmzeit mindestens einen Prozess hatte; parallel geöffnete Apps
    zählen jeweils voll. Hilfsprozesse im App-Verzeichnis zählen zur App, Spiele aus der
    Steam-Bibliothek zu Steam. Vordergrundzeit (KWin-Skript) ggf. später.
- Protokoll **blockierter** Aktionen (gesperrtes Programm, gesperrte Domain).
  - Domains: Unbound protokolliert lokale Aktionen in eine Datei im RAM (`/run/kidctl-dns/<kind>`);
    der Agent liest und leert sie alle 5 s und übernimmt nur Sperren – erlaubte Anfragen werden
    verworfen, nie gespeichert. Ins Protokoll: eigene Sperren, DoH-Anbieter, Inhaltslisten
    (NSFW/Glücksspiel/Porn), im Whitelist-Modus alles Nicht-Freigegebene; **nicht** Werbe-/Tracker-
    Listen (zu viele Einträge). Je Domain höchstens alle 10 min.
- **Kein** Verlauf erlaubter Websites.
- Push-Alarme:
  - Gerät hat sich länger als X Stunden nicht gemeldet (Standard 48 h, pro Gerät einstellbar).
    Prüfung alle 5 min, ein Alarm pro Offline-Phase (erneut erst nach erneuter Meldung).
  - Integritätsfehler des Agenten (z. B. nftables-Regeln fehlen, PAM-Konfiguration verändert).
    Umsetzung: Agent prüft alle 60 s (und nach jedem Verbindungsaufbau) PAM-Block, nsswitch,
    Browser-Policies, noexec (`/tmp`, `/var/tmp`, `/dev/shm`, Kinder-Homes), Resolver je Kind und
    nftables-Umleitung/DoT-Sperre je Kind-UID. Selbst verwaltete Teile (nftables, Resolver, Mounts)
    stellt er sofort wieder her; Dateien unter `/etc` meldet er nur (Agent läuft mit
    `ProtectSystem`). Meldung an den Server nur bei Änderung; Push-Alarm nur für **neu**
    fehlschlagende Prüfungen. WebUI: Abschnitt „Integrität“ je Gerät, Hinweis in Übersicht/Geräteliste.

## Verteilung & Updates

- Client als PKGBUILD: Agent, Tray-App, systemd-Unit, PAM-Konfiguration, nftables-Regeln,
  Firefox/Chromium-Policies, Mount-Konfiguration (`noexec`, Steam-Bind-Mount).
- Eigenes, GPG-signiertes Pacman-Repository als statisches Verzeichnis im Docker/Traefik-Stack.
- Updates über normales `pacman -Syu`.
- Umsetzung (2026-10-14, entschieden: lokal bauen, eigener Schlüssel): `packaging/repo/`
  - `kidctl-repo-key.sh`: Ed25519-Signierschlüssel in eigenem GPG-Ordner
    (`~/.local/share/kidctl-repo/gnupg`), Export des öffentlichen Schlüssels.
  - `kidctl-repo-build.sh`: `makepkg --sign` (mit `--nodeps`, reines Python) + `repo-add --sign`.
  - `kidctl-repo-publish.sh`: `rsync` nach `./data/repo` des Stacks; Server liefert es unter `/repo/`.
  - `kidctl-repo-client.sh`: Schlüssel holen, **gegen Fingerabdruck prüfen**, `pacman-key --lsign-key`,
    `[kidctl]` mit `SigLevel = Required DatabaseRequired` (Sicherung `pacman.conf.kidctl.bak`).
- Protokollversion im WebSocket-Handshake; Server unterstützt mindestens eine ältere Version.
  Zu alter Agent → Hinweis im WebUI, Durchsetzung läuft mit letzter Policy weiter.

## Bewusst nicht umgesetzt / Restlücken

- Kein Schutz gegen physischen Zugriff (nur Härtungs-Anleitung `docs/haertung.md`).
- Keine Leerlauferkennung.
- Keine Unterstützung anderer Desktops als KDE Plasma (Wayland).
- Kein `flatpak --user`, keine Pro-Spiel-Steuerung bei Steam.
- „Nicht-Steam-Spiel hinzufügen“ als bekannte Restlücke. Gleiche Wirkung: Die Steam-Bibliothek
  gehört dem Kind (Steam aktualisiert sich selbst) – `steam.sh`/Client sind ersetzbar. **Wer Steam
  freigibt, gibt faktisch alles frei, was über Steam gestartet werden kann.**
- Shell-Befehle (`sh -c …`) über KRunner o. Ä. sind erlaubt (Systemkomponenten brauchen das);
  ausführbar sind damit aber nur erlaubte Programme – eigene Programme scheitern an `noexec`,
  Interpreter-Code (`python -c`, Skripte aus dem Home) an der Interpreter-Regel.
- Shell-Skripte aus `/usr`, `/etc`, `/opt` sind immer erlaubt (das Programm, das sie starten,
  wird geprüft); reine Shell-Skript-Anwendungen unter `/usr` sind daher nicht sperrbar.
- Programme, die freigegebene Apps intern aufrufen (z. B. `git` aus Kate), sind gesperrt, solange
  sie nicht selbst freigegeben werden (`git` bewusst nicht in der Basisliste: Aliase/Hooks).
- Keine IP-Bindung im ersten Release: direkte IP-Adressen (auch DoH über IP) umgehen den
  DNS-Filter.
- Programme, die systemd-resolved über D-Bus/Varlink fragen (z. B. `resolvectl`), umgehen die
  Umleitung; Browser und glibc-Programme nicht (nsswitch angepasst).
- Zwischen zwei Integritätsprüfungen (≤ 60 s) kann eine entfernte nftables-Tabelle den DNS-Filter
  offen lassen. Hinweis: `nftables.service` leert beim (Neu-)Laden den gesamten Regelsatz
  (`flush ruleset`) – der Agent stellt seine Tabelle danach im nächsten Takt wieder her.
- Eine Freigabe-Ausnahme im Blacklist-Modus gilt nicht für spezifischere Einträge einer Sperrliste
  (z. B. Ausnahme `example.com`, Liste sperrt `ads.example.com`).
- Kein Browserverlauf, keine Bilder/Dateien im Chat, keine Lesebestätigungen.
- Kein Selbst-Update des Agenten (nur über Pacman).

## Offene Punkte

- Subdomain (Annahme `kids.nossco.de`).

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
- 2026-10-13: Paketierung Client (`packaging/arch/`): PKGBUILD, `/usr/bin/kidctl` (Python `-E -s`),
  `kidctl-agent.service` (`Type=notify` nach erster Sperrliste, vor `systemd-user-sessions`,
  `Restart=always`, State-/Runtime-Directory, leichte Härtung), Install-Skript setzt/entfernt
  PAM-Block, SDDM-Theme als Paketdateien; Agent meldet Bereitschaft per sd_notify. Getestet:
  3 neue pytest-Tests (gesamt 27: sd_notify, PKGBUILD-Version = Agent-Version, Unit-Ordnung ohne
  network-online). In der VM ab Snapshot `vor-pam`: `makepkg` + `namcap` (verbleibende Meldungen
  bewertet), `pacman -U` (PAM-Block + Theme + Hinweis), `enable --now` (bereit nach Sperrliste,
  `kind` abgelehnt, `admin` erlaubt), Neustart (Agent bereit bei 8,95 s, Logins ab 11,99 s, `kind`
  sofort gesperrt trotz anfangs fehlendem Netz), `kill -9` (Sperrliste bleibt, Neustart durch
  systemd), `pacman -R` (system-login identisch mit Original, Theme/Dienst weg, Token bleibt),
  Neuinstallation.
- 2026-10-13: Tray-App (`tray/`, PySide6) + Agent-Seite (Tray-Socket mit SO_PEERCRED, Chat-Cache,
  Ausgangs-Warteschlange, Benachrichtigungen, Aktionsknopf), lokales Protokoll
  `common/kidctl_common/tray.py` (Version 1), Paket 0.2.0 (`kidctl-tray`, XDG-Autostart, Abhängigkeit
  `pyside6`). Getestet: Agent 35 pytest-Tests (neu: Tray-Socket hello/Verlauf, nicht zugeordneter
  User, Offline-Warteschlange + Persistenz + Flush + Ack, Push/Benachrichtigungen inkl. kein Spam
  beim Erstabgleich, chat_sync, Dialog-Aktion, ungültige Eingaben; E2E Tray → Agent → echter
  Server → Eltern-Chat → Antwort/angepasste Genehmigung → Tray + Policy-Gutschrift); Tray 3
  Text-Tests (Host) + 5 inkl. GUI offscreen gegen Fake-Agent (VM, PySide6 6.12). VM: Paket-Upgrade
  (Agent per post_upgrade neu gestartet, Socket 0666), SO_PEERCRED mit echten Usern (`admin` →
  nicht eingeschränkt + Chat abgelehnt; `kind` → Zustand „Max“, Nachricht beim Server).
- 2026-10-13: Tray-App im VM-Fenster vom Nutzer bestätigt (Plasma, Paket 0.2.0): Symbol mit
  Restminuten + Farbe, Chat senden, Verlängerung anfragen („Wartet auf Antwort“), Eltern-Antwort
  als KDE-Benachrichtigung und im Chat, Genehmigung (+15 min) als Benachrichtigung, Status im Chat
  und sofort höhere Restzeit im Symbol. Noch nicht sichtbar geprüft: Klick auf den Knopf
  „Verlängerung anfordern“ in einer Warnung.
- 2026-10-13: Programm-Whitelist Teil 1 (Server + Agent, Paket 0.3.0). Getestet: Server 44 pytest-
  Tests (neu: Katalog/Auswahl → Policy, ungültige Schlüssel/Modus, Katalog-Ersatz, Sperrprotokoll
  inkl. nicht zugeordneter User, Migration `child.program_mode`); Agent 49 (neu: Inline-Code-
  Erkennung, Entscheidungsregeln inkl. App-Verzeichnis/Flatpak/Shell/Interpreter, .desktop-Parser,
  Netlink-Parser, Watcher Sperren/Protokollieren/Aus/nicht zugeordnet, Policy-Wechsel, pacman-
  Parser/Abhängigkeitskette/Filter, Ausschluss vs. Eltern-Freigabe, Rückfall auf Protokollieren
  ohne Basisliste). VM: Netlink aktiv, Katalog (21 Apps) im WebUI, Basisliste 2156 Programme;
  Mitschnitt einer laufenden Plasma-Sitzung von `kind` im Modus „Nur protokollieren“ meldet nur
  noch die mit altem Wrapper (`python -c`) gestartete Tray-App. Noch ausstehend: Nutzung im
  Protokollmodus, Modus „Sperren“ in der VM, Steam, `noexec`-Mounts.
- 2026-10-13: Programm-Whitelist Fehlerbehebung: Apps aus Plasma 6 starten als systemd-Unit
  (`systemd --user` → fork → exec `systemd-executor` → exec App, gleiche PID/Startzeit). Prozesse
  galten nach dem ersten (erlaubten) exec als geprüft – Apps aus dem Menü wurden nie erfasst.
  Kennung „geprüft“ umfasst jetzt Programm und Argumente. Gefunden im VM-Test (Kate fehlte im
  Protokoll, nur deren Kindprozess `git`). Getestet: 2 Regressionstests (gesamt 51); VM: Dolphin per
  KIO in der Sitzung von `kind` gestartet → sofort protokolliert. Außerdem Log-Zeilen gedrosselt wie
  das Sperrprotokoll.
- 2026-10-13: Programm-Whitelist Modus „Sperren“ in der VM: Plasma-Sitzung bleibt intakt
  (kwin, plasmashell, ksmserver, pipewire, Xwayland), nicht freigegebene App (Dolphin, per KIO wie
  aus dem Menü gestartet) sofort beendet. Fehler gefunden und behoben: der Agent sperrte sein
  eigenes `notify-send` (läuft mit Kinder-UID, `libnotify` nicht in der Plasma-Kette) – `notify-send`
  und `busctl` fest in der Basisliste, Test prüft das (Agent gesamt 52). Zweite Nutzungsrunde im
  Protokollmodus (Menü, KRunner, Sperrbildschirm, Lautstärke, Datei öffnen): nur Apps und deren
  Hilfsprogramme protokolliert, keine Plasma-Bestandteile. `git` (von Kate aufgerufen) bleibt
  bewusst gesperrt (Aliase/Hooks führen beliebige Befehle aus).
- 2026-10-13: Programm-Whitelist – Login im Modus „Sperren“ repariert (Nutzer konnte sich nicht
  mehr als `kind` anmelden). Ursachen und Behebung:
  1. SDDM startet die Sitzung über `bash --login /usr/share/sddm/scripts/wayland-session` mit dem
     Sitzungs-TTY als Steuerterminal → galt als interaktive Shell. Neue Shell-Regel nach Argumenten:
     `-c` oder Skript aus /usr, /etc, /opt erlaubt; ohne Skript, `-i`, `-s` (z. B. TTY-Login) oder
     Skript aus dem Home gesperrt.
  2. Race beim Lesen von /proc während exec(): Programm und Argumente passten nicht zusammen bzw.
     Argumente leer → Tray-Wrapper (`/bin/sh /usr/bin/kidctl-tray`) als interaktive Shell beendet.
     Jetzt konsistentes Lesen (Programm vor/nach den Argumenten) und erneutes Lesen bei leeren
     Argumenten; dauerhaft leer → weiterhin gesperrt.
  3. `xrdb` (Xwayland-Start) ruft `cpp`/`cc1` auf, wenn gcc installiert ist → in die Basisliste.
  Lehre: Der Mitschnitt im Protokollmodus muss einen kompletten Login enthalten. Getestet: 6 neue
  Regressionstests (Agent gesamt 55); VM mit Autologin (nur für den Test): Login im Protokollmodus
  (nur `cpp`/`cc1` gemeldet), dann Login im Modus „Sperren“: kwin, plasmashell, ksmserver, pipewire,
  Xwayland und Tray-App laufen, nichts gesperrt.
- 2026-10-13: Programm-Whitelist Modus „Sperren“ vom Nutzer im VM-Fenster bestätigt: nicht
  freigegebene Apps (Dolphin, Systemeinstellungen, Konsole) werden sofort beendet mit
  Benachrichtigung, freigegebene (Firefox, Kate) funktionieren, Tray-App läuft, Ab-/Anmelden mit
  Passwort funktioniert. Sperrprotokoll im WebUI gefüllt.
- 2026-10-14: Programm-Whitelist Teil 3 – noexec und Steam (Paket 0.4.0). Entscheidungen des Nutzers:
  noexec nur für Kinder-Homes, zusätzlich `/var/tmp` und `/dev/shm`, Steam per Symlink statt
  Bind-Mount. Agent: `kidmounts.py` (noexec-Homes über `systemd-mount`, Steam-Bibliothek + Link als
  Kind), Steam-Regeln mit lazy ausgewerteter Elternkette; Paket: `kidctl-noexec.sh`/`.service`.
  Fehler im VM-Test gefunden: Starter ruft `steam.sh` per exec auf → Einstiegspunkte bei Freigabe
  erlaubt. Getestet: Agent 61 pytest-Tests (neu: sichere Home-Erkennung, Mount/Unmount nur eigener
  Mounts, Steam-Bibliothek + Link als User, Steam-Regeln inkl. Regression exec-Starter; Tests
  ersetzen alle Mount-Befehle durch Attrappen). VM (Snapshot `vor-noexec` vorher): `/tmp`, `/var/tmp`,
  `/dev/shm` und Kinder-Home noexec (Start eigener Programme → Kernel „Permission denied“), Admin-Home
  ausführbar; Plasma-Login mit noexec-Home inkl. Tray ohne Fehler; nach Neustart Reihenfolge noexec
  6,0 s → Kinder-Home 9,66 s → Agent 9,68 s → Logins 9,70 s; Programmkontrolle aus → Mount aufgehoben.
  Steam mit nachgebauter Struktur (Starter → steam.sh per exec → Client → Spiel): über Steam läuft,
  Spiel direkt gestartet → gesperrt, Freigabe entzogen → Client/Spiel beendet und Neustart gesperrt;
  Nachbau danach entfernt. **Nicht getestet:** echtes Steam mit Spiel (braucht multilib + Konto).
  Außerdem dauerhafte Entwicklungsumgebung (`dev/devserver.sh`, `dev/webctl.py`), nachdem Scratch-
  Daten unter /tmp beim Host-Neustart verloren gingen.
- 2026-10-14: Website-Filter (Server + Agent, Paket 0.5.0). Entscheidungen: Bundles YouTube,
  Wikipedia, Lern-Apps, Microsoft 365; Schulplattform später; Vorauswahl HaGeZi Multi + NSFW;
  SafeSearch je Kind (Standard an); Unbound statt Blocky (offizielle Repos). Getestet: Server 51
  pytest-Tests (neu: Domain-Normalisierung, Listenformate, Einstellungen → Policy für
  Whitelist/Blacklist/Aus, ungültige Eingaben, Listen-Download über simulierten HTTP-Server, Abruf
  nur mit Geräte-Token + ETag/304, Migration `web_mode`/`safesearch` mit Alt-Schema); Agent 68
  (neu: Zonen-Rangfolge ohne Doppelte, SafeSearch nur für Freigegebenes, Unbound-Konfiguration,
  nftables-Regeln, Upstream aus resolv.conf, Listenabruf mit Prüfsumme/Cache, Anwenden mit Neustart
  nur bei Änderung und Aufräumen). VM (Snapshot `vor-dns`): nsswitch angepasst, Policies installiert;
  Whitelist (Wikipedia + google.com): Wikipedia ja, example.com/dns.google/YouTube nein,
  www.google.com → forcesafesearch; DoT für `kind` verweigert, für `admin` möglich; Blacklist mit
  echten Listen (250 844 Regeln, ≈ 110 MB, Start 0,4 s): Werbe-/NSFW-Domain und tiktok.com gesperrt,
  example.com erreichbar, YouTube eingeschränkt, Bing strict; nach Neustart Filter aus dem Cache vor
  Serververbindung aktiv (Resolver 9,66 s, Logins 9,79 s). Fehler gefunden: Unbound brauchte
  `AF_NETLINK` (getifaddrs) – dabei bestätigt: ohne Resolver hat das Kind kein DNS (sperrt
  geschlossen). **Nicht getestet:** Firefox/Chromium-Policy-Wirkung im Browser (Sichttest).

- 2026-10-14: Website-Filter im Browser vom Nutzer bestätigt (VM, Firefox als `kind`, Blacklist +
  SafeSearch): `about:policies` zeigt DNSOverHTTPS gesperrt, gesperrte Domain nicht erreichbar,
  normale Seiten laden, Google-SafeSearch und YouTube eingeschränkt aktiv.
- 2026-10-14: Integritätsprüfung (Agent + Server, Paket 0.6.0). Getestet: Agent 72 pytest-Tests (neu:
  Dateiprüfungen PAM/nsswitch/Browser-Policies ok/kaputt/fehlend, noexec-Prüfungen, nftables-Regel
  nur zusammenhängend gültig, Reparatur + Meldung nur bei Änderung, /etc-Fehler nur gemeldet);
  Server 53 (neu: Alarm nur bei neu fehlschlagender Prüfung, erneuter Alarm nach Behebung +
  Wiederauftreten, Anzeige Geräteseite/Liste/Übersicht, Migration `device.integrity_*`). VM: nach
  Upgrade alle 6 Prüfungen grün; nftables-Tabelle gelöscht → nach ≈ 45 s vom Agenten
  wiederhergestellt, tiktok.com für `kind` wieder gesperrt; PAM-Block entfernt → im nächsten Takt
  gemeldet, Server protokolliert Integritätsfehler und löst Alarm aus (Push im Dev-Server nicht
  konfiguriert); nach Wiederherstellung wieder „keine Fehler“.
- 2026-10-14: Auswertung (Server + Agent, Paket 0.7.0): Programmzeit, Statistikseite, gesperrte
  Domains im Sperrprotokoll. Getestet: Agent 80 pytest-Tests (neu: Zuordnung Prozess → App inkl.
  App-Verzeichnis/Flatpak/Skript/Steam, Programmzeit nur einmal je App und nur für gezählte User,
  Persistenz, Unbound-Logzeilen, Protokollquellen ohne Werbelisten, Log lesen + leeren, Drosselung);
  Server 56 (neu: Programmzeit-Bericht mit Maximum-Regel/nicht zugeordnet/zu alt, Statistikseite mit
  14 Säulen, Tooltip inkl. Überziehung, Programmliste je Zeitraum, Domain-Einträge im Protokoll,
  Listen-Merkmal „ins Protokoll“). Diagrammfarbe `#2a78d6` auf Weiß 4,42:1, Kontingent-Markierung
  7,94:1 (WCAG berechnet; Node für den Palettenvalidator nicht vorhanden – bei einer Reihe nur
  Kontrast relevant). Gerendert mit Beispieldaten und angesehen (Achsenbeschriftung abgeschnitten →
  korrigiert). VM: tiktok.com und dns.google als `kind` → je ein Protokolleintrag mit Quelle,
  Resolver-Log danach leer; bei gesperrtem Bildschirm keine Programmzeit, nach Entsperren Firefox
  erfasst und gemeldet.
- 2026-10-14: Pacman-Repository (Skripte + Auslieferung unter `/repo/`, Paket 0.8.0). Getestet mit
  Wegwerf-Schlüssel im Scratchpad (echter Schlüssel nicht angelegt): Schlüssel erzeugen (Dry-Run/
  Apply), Bauen auf dem Entwicklungsrechner (ohne Installation; `--nodeps` nötig, da Laufzeit-
  Abhängigkeiten dort fehlen), Paket- und DB-Signatur gültig, Veröffentlichen per rsync (Dry-Run/Apply),
  Auslieferung inkl. Symlinks (pytest: 57 Server-Tests). VM (Snapshot `vor-repo`): falscher
  Fingerabdruck abgelehnt; Einrichtung + `pacman -Syu` → Upgrade 0.7.0 → 0.8.0 „Validated By:
  Signature“, Agent läuft; manipulierte DB-Signatur → pacman verweigert die Synchronisation. Danach
  VM auf `vor-repo` zurückgesetzt (Testschlüssel nicht mehr vertraut). Außerdem `vm.sh stop` fährt
  per SSH herunter (powerdevil fing den ACPI-Knopf bei angemeldeter Plasma-Sitzung ab).
- 2026-10-14: Projektname KidCTL und Lizenz PolyForm Noncommercial 1.0.0 festgelegt: `LICENSE` mit
  „Required Notice“ (Adrian Noss als ursprünglicher Autor), PKGBUILD `license=(PolyForm-Noncommercial-1.0.0)`
  und Installation nach `/usr/share/licenses/kidctl/`. Anzeigename „KidCTL“ in WebUI, PWA-Manifest,
  Push-Testnachricht, TOTP-Issuer (nur neue QR-Codes), Tray, .desktop und SDDM-Theme; Paket-, Befehls-,
  Pfad- und Unit-Namen bleiben `kidctl`. Getestet: Server-/Agent-/Tray-Tests, Paketbau mit Lizenzdatei.
- 2026-10-14: Härtungs-Anleitung `docs/haertung.md`: Konten prüfen/root sperren, Firmware-Passwort und
  Bootreihenfolge, Bootloader (GRUB-Passwort mit `--unrestricted`-Skript + Pacman-Hook, systemd-boot
  `editor no`, Limine `editor_enabled: no`), optional LUKS und Secure Boot (sbctl, UKI) + TPM-Entsperrung
  (PCR 7). Getestet in der VM (GRUB 2.16, BIOS): Standardeintrag startet ohne Passwort, `e`/`c`/
  „Erweiterte Optionen“ verlangen Anmeldung, falsches Passwort abgelehnt; Neuinstallation von `grub`
  setzt `10_linux` zurück, Hook stellt `--unrestricted` wieder her; gesperrtes root +
  `systemd.unit=emergency.target` → keine Shell, normaler Start. Nicht getestet (UEFI/TPM nötig):
  systemd-boot, Limine, Secure Boot, TPM. VM danach auf `vor-haertung` zurückgesetzt.
- 2026-10-14: Dokumentation: `README.md` (Überblick, Funktionen, Architektur, Struktur, Entwicklung,
  Lizenz) und `docs/installation.md` (Server mit Docker/Traefik inkl. Datenordner-Rechten, .env,
  VAPID, erster Eltern-Account, PWA; Pacman-Repo; Kinderrechner: Accounts, Repo, Paket, Kopplung,
  Zuordnung; WebUI-Einstellungen; Updates; Deinstallation). `kidctl-repo-build.sh` legt
  `kidctl-repo-client.sh` mit ins Repo (abrufbar unter `/repo/kidctl-repo-client.sh`; vorher
  wegen `rsync --delete` dort nicht dauerhaft ablegbar). Getestet: Syntaxprüfung (`bash -n`).
- 2026-10-14: Fix Docker-Deployment: `httpx` (Sperrlisten-Download) fehlte in `server/requirements.txt`
  (lokal nur als Testabhängigkeit installiert) → Container startete nicht. Zusätzlich `tzdata` für
  `zoneinfo` im schlanken Image. Getestet: frische venv nur mit `requirements.txt`, Import aller Module,
  uvicorn-Start („startup complete“), `/login` 200.
- 2026-10-14: Sicherheitsprüfung Agent ↔ Server (Abschnitt „Sicherheit Agent ↔ Server“). Behoben:
  (1) Benutzernamen aus der Policy ungeprüft in die PAM-Sperrliste – mit Zeilenumbruch hätte ein
  kompromittierter Server root/Admins aussperren können; (2) Sperrlisten-ID ungeprüft als Dateiname
  (`../` → Schreiben außerhalb des Caches als root); (3) Domains ungeprüft in der Unbound-Konfiguration
  (eingeschleuste Anweisungen); (4) Kind-ID ohne Obergrenze (Port-Überlauf → nftables-Fehler, Filter
  aus); (5) Kopplung/Verbindung per `http://` möglich (Token im Klartext) – jetzt nur mit
  `--insecure-http`; (6) Sperrlisten-Download folgte Weiterleitungen samt Token; (7) `notify-send` ohne
  `--`. Server verteilt keine ungültigen Benutzernamen. Version 0.9.0. Getestet: 30 neue Agent-Tests
  (`test_hardening.py`: Ablehnung der Angriffswerte, Sperrliste ohne eingeschleuste Namen/Admins,
  Unbound-Zonen nur gültige Domains, HTTPS-Pflicht, keine Weiterleitung), Agent 110, Server 57.
- 2026-10-14: WebUI neu gestaltet: warmes Papier-Grau, ein Akzent (Tannengrün), Serifen-Überschriften,
  Systemschriften (keine Webfonts/Drittanfragen), automatischer Dunkelmodus. Aktionen als Buttons statt
  Text-Links (Navigation, Chat/Statistik/Einstellungen, Zurück), Status als Pills, einheitlich gezeichnete
  Checkboxen; Bundles/Sperrlisten als Auswahlkacheln, je nach Filtermodus nur die passenden Abschnitte
  (reines CSS `:has`). Getestet: Server-Tests (57, Markup-Prüfungen angepasst), Screenshots hell/dunkel/
  Handy (Firefox headless) von Übersicht, Kind, Statistik, Gerät, Chat.
- 2026-10-15: Tray-Chat im dunklen KDE-Theme (Breeze Dark) unlesbar: Blasenfarben waren fest hell, die
  Schrift kam aus dem Theme (weiß). Jetzt Hintergrund und Schrift aus der Systempalette (eigene Blase:
  Base + 25 % Highlight, fremde: Base + 8 % Text), Neuzeichnen bei Theme-Wechsel. Version 0.9.1.
  Getestet: Tray-Tests (7, inkl. GUI-Ablauf und Kontrast ≥ 4,5:1 für Breeze/Breeze Dark) mit PySide6 in
  temporärer venv, Sichtprüfung per Offscreen-Rendering hell/dunkel.
- 2026-10-15: Sichttest Tray-Chat mit Breeze Dark auf echtem Testrechner (0.9.1 aus dem Pacman-Repo) vom Nutzer bestätigt.
- 2026-10-15: „Jetzt sperren“ (Abschnitt unter Bildschirmzeit): Server `app/lock.py`, Spalten
  `child.locked`/`locked_until` (Migration), Routen `/children/<id>/lock|unlock`, Policy-Felder; Agent-Regel
  `locked` vor Kontingent/Fenster, Enforcer-Texte „Gesperrt“/„Wieder freigegeben“, Tray-Text, PAM-Hinweis.
  Version 0.10.0. Getestet: Server 63 (Sperre/Freigabe mit Policy-Push über WebSocket, befristet + Ablauf,
  ungültige Dauer, CSRF, „bis morgen“ in Sommer-/Winterzeit, Migration Alt-Schema), Agent 115 (Regel,
  Ablauf offline, naive Zeit, Enforcer-Countdown + Abbruch bei Freigabe), Tray 7; Screenshots Übersicht/
  Kind-Seite gesperrt und frei.
- 2026-10-16: Vorwarnzeit für „Jetzt sperren“ konfigurierbar (Spalte `child.lock_grace_minutes`,
  Migration; Policy `lock_grace_seconds`; Enforcer mit Erinnerung 1 min vorher, 0 = sofort). Version 0.11.0.
  Getestet: Server 66 (Policy-Wert, Schnellknopf = 1 min, ungültige Werte → 400 ohne Sperre, Migration),
  Agent 118 (10 min: Hinweis, Erinnerung, Abmeldung genau nach Ablauf; 0 = sofort; Zeitende unverändert),
  Tray 7.
- 2026-10-16: Sichttest „Jetzt sperren“ mit Vorwarnzeit auf echtem Testrechner (0.11.0) vom Nutzer bestätigt.
- 2026-10-16: Fix: Programmkontrolle „Sperren“ → „Aus“ bei angemeldetem Kind führte zu eingefrorenem
  Desktop/schwarzem Bildschirm (Nutzer-Test). Ursache: sofortiges `systemd-umount` des noexec-Homes
  während der Sitzung. Jetzt aufgeschoben bis zur Abmeldung (`KidMounts.deferred`, Prüfung je Takt).
  Version 0.11.1. Getestet: Agent 121 (Aufschub bei Anmeldung, Aushängen nach Abmeldung, Aufschub
  verworfen bei erneutem Einschalten, Erkennung über /run/user/<uid>).
- 2026-10-16: Basisliste um Paketgruppen erweitert (`group:plasma`, `group:kf6`; Mitglieder aus dem
  Feld „Groups“ von `pacman -Qi`), nach Nutzer-Test: KDE-Hintergrundprogramme mussten mühsam einzeln
  freigegeben werden. Version 0.12.0. Getestet: Agent 125 (Gruppenauflösung, Datei-Format, Berechnung
  mit Gruppen, socat-Ausschluss); Probe auf dem Entwicklungsrechner (Plasma, nur pacman-Abfrage): 2188 →
  2304 Programme, u. a. kded6, kwalletd6, org_kde_powerdevil, kactivitymanagerd, polkit-kde-Agent,
  xembedsniproxy, plasma-browser-integration-host; keine Shells/Terminals neu.
