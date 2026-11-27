# Parental Control für Arch Linux – Spezifikation

Status: Anforderungen per /grill-me abgestimmt (2026-10-13). Serverseite für Bildschirmzeit, Chat/Verlängerungen, PWA/Web Push, Policy-Verteilung und Offline-Alarm umgesetzt; Agent (Bildschirmzeit, PAM-Login-Sperre), Tray-App und Paket umgesetzt.
Projektname: **KidCTL** (Paket, Befehle und Pfade technisch `kidctl`). Deploy unter `app.kidctl.de` (bis 2026-11-21 `kids.nossco.de`).
Lizenz: AGPL-3.0-or-later (`LICENSE`, seit 2026-11-10; vorher PolyForm Noncommercial 1.0.0) – Open Source
nach OSI, Voraussetzung für eine kostenlose Code-Signatur über die SignPath Foundation (Antrag 2026-11
wegen zu geringer Bekanntheit abgelehnt, Installer vorerst unsigniert; `docs/code-signing.md`). Die Weboberfläche verlinkt den Quelltext (`SOURCE_URL`, AGPL §13).

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
  Härtungs-Anleitung dokumentiert: `docs/hardening.de.md` (root sperren, Firmware-/Bootloader-Passwort,
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
- Unterstützte Umgebung: **KDE Plasma und GNOME, jeweils auf Wayland** (GNOME ergänzt 2026-10-21,
  siehe „Desktop-Umgebungen“). X11 und andere Desktops werden nicht unterstützt (Cinnamon und COSMIC
  folgen als eigene Schritte, siehe „Debian/Ubuntu“).
- Distributionen: Arch Linux sowie Debian 13, Ubuntu 24.04/26.04 und darauf aufbauend (siehe „Debian/Ubuntu“).
- Windows 10/11 (entschieden 2026-11-04, Schritt 1 umgesetzt, siehe „Windows“): Windows-Dienst statt systemd, Named Pipe
  statt Unix-Socket, Konto-Sperre statt PAM.

### Desktop-Umgebungen (entschieden 2026-10-21)
- Plasma (SDDM) und GNOME (GDM) auf Wayland; beide dürfen auf demselben Rechner installiert sein. Der Agent
  erkennt den Desktop je Sitzung: logind `Desktop` (SDDM trägt `KDE` ein, GDM nichts), sonst die laufende
  Shell des Kindes (`gnome-shell` bzw. `plasmashell`).
- Pakete (Split-PKGBUILD): `kidctl` (Agent, Tray, Login-Sperre, Filter), `kidctl-plasma` (SDDM-Theme;
  hängt von `sddm`, `plasma-desktop` ab), `kidctl-gnome` (`gnome-shell-extension-appindicator` als
  Voreinstellung für alle User per Schema-Override `90_kidctl.gschema.override`, Eintrag „KidCTL“ in der
  Anwendungsübersicht). Beide legen `/usr/share/kidctl/desktop/<desktop>` an; fehlt das Paket zu einem
  installierten Desktop (`plasmashell` bzw. `gnome-shell` vorhanden), Hinweis bei pacman und
  Integritätsprüfung `desktop:<desktop>`. Bestehende Plasma-Rechner: einmalig `pacman -S kidctl-plasma`.
- Tray unter GNOME: Symbol über die AppIndicator-Erweiterung (nicht gesperrt – eine dconf-Sperre gälte für
  alle User). Die Tray-App meldet dem Agenten im `hello` (`tray_icon`), ob ein Tray-Symbol möglich ist
  (alle 30 s geprüft, bei Änderung neues `hello`); nicht sichtbar → Integritätsprüfung `tray:<user>`.
  „KidCTL“ in der Anwendungsübersicht startet `kidctl-tray --show`: Läuft die Tray-App schon, öffnet sie
  ihr Fenster (Socket `$XDG_RUNTIME_DIR/kidctl-tray.sock`, eine Instanz je User). Das Fenster zeigt
  Restzeit, Chat, „Verlängerung anfordern“ und „Gesperrt – Freigabe anfragen“. Ohne Tray-Symbol erscheinen
  Hinweise der Tray-App als Dialog. Symbol `kidctl` (eigenes SVG) für Tray-Einträge und Benachrichtigungen.
- Abmeldung GNOME: `org.gnome.SessionManager.Logout(1)` (ohne Rückfrage). Ist Plasma installiert, startet
  D-Bus `org.kde.Shutdown` auch in einer GNOME-Sitzung und meldet Erfolg – daher entscheidet die laufende
  Shell, ohne Treffer zuerst GNOME, dann Plasma. Danach wie bisher hart nach 20 s.
- Login-Sperre unter GDM: `gdm-password`/`-autologin`/`-fingerprint`/`-smartcard` binden `system-local-login`
  → `system-login` ein, der kidctl-Block greift. GDM zeigt PAM-Infotexte (`pam_echo`) nicht an, wohl aber
  Fehlermeldungen; das SDDM-Theme sieht umgekehrt nur Infotexte. Der Block gibt den Hinweis daher je Dienst
  aus: GDM-Dienste per `pam_nologin file=/run/kidctl/locked-message` (Fehlermeldung, lehnt ab), alle
  anderen wie bisher per `pam_echo` (Unterscheidung `pam_succeed_if service notin …`). GNOME entsperrt den
  Sperrbildschirm über `gdm-password` – die Login-Sperre gilt dort auch (unkritisch, die Sitzung wird bei
  Zeitende ohnehin beendet).
- GDM-Autologin für Kinder nicht unterstützt: Lehnt PAM die automatische Anmeldung beim Start von GDM ab,
  startet GDM keinen Anmeldebildschirm (schwarz; in der VM auch ohne kidctl mit abgelaufenem Konto
  nachgestellt). Integritätsprüfung `gdm-autologin:<user>` meldet `AutomaticLogin` eines Kindes in
  `/etc/gdm/custom.conf`.
- Sprache: GNOME übernimmt die Kontosprache (AccountsService) beim Login in die Sitzungsumgebung; der Agent
  liest `gnome-shell`/`gnome-session-binary`, ohne Sitzung `Languages=`/`Language=` aus
  `/var/lib/AccountsService/users/<user>`, sonst `/etc/locale.conf`.
- Basisliste: Gruppe `gnome` und `gdm`, `gnome-shell`, `mutter`, `gnome-session`, `gnome-settings-daemon`,
  `xdg-desktop-portal-gnome`, `gnome-keyring`; Hilfsskripte unter `/usr/share/gnome-shell/` (Dienste von
  GNOME Shell, Werkzeuge systemweiter Erweiterungen). Sitzungsdienste (D-Bus, systemd --user) zählen jetzt
  auch mit Programm unter `/usr/share` (gjs-Skripte). Terminals `kgx`, `gnome-terminal`, `ptyxis` nie Basis.
  `gjs`/`gjs-console` und die SpiderMonkey-Shell (`js<N>`) gelten als Interpreter (`-c`/`-e` = Inline-Code,
  `GJS_PATH` außerhalb des Systems = eingeschleust) – GNOME-Apps wie Wetter oder Karten sind gjs-Skripte und
  werden über ihr Skript freigegeben.
- Dienste beim Login ohne Benachrichtigung: Startet der systemd-Benutzerdienst eines Kindes innerhalb von
  90 s nach Beginn einer Anmeldung (Start des Sitzungsleiters laut logind; neue Sitzung noch nicht gelesen
  → wie Anmeldung) ein nicht freigegebenes Programm, wird es gesperrt und protokolliert, aber ohne Popup
  und ohne Eintrag zum Anfragen (z. B. GNOME Software als Hintergrunddienst, der als App gilt).
- GNOME-Suche: Die Shell startet beim Tippen die Suchanbieter der Apps per D-Bus. Für Kinder im Modus
  „Sperren“ trägt der Agent die Anbieter nicht freigegebener Apps (`/usr/share/gnome-shell/search-providers`,
  `DesktopId`) per `gsettings` als Kind in `org.gnome.desktop.search-providers disabled` ein und nimmt sie
  nach einer Freigabe bzw. ohne Programmkontrolle wieder heraus; Einträge des Kindes bleiben
  (`gnome-search.json`). Nur bei laufender GNOME-Sitzung, nur bei Änderung.

### Debian/Ubuntu (entschieden 2026-10-21)
- Unterstützt: Ubuntu 24.04 LTS und Systeme darauf (Kubuntu, Mint 22, Pop!_OS 22.04/24.04), Debian 13,
  Ubuntu 26.04; amd64. Ein Paket für alle.
- Desktops: GNOME und Plasma (Wayland) – umgesetzt; Cinnamon mit LightDM (auch X11, nur Cinnamon) für Mint
  und COSMIC für Pop!_OS 24.04 folgen als eigene Schritte. Desktop-Pakete wie unter Arch einzeln
  (`kidctl-gnome`, `kidctl-plasma`, später `kidctl-cinnamon`, `kidctl-cosmic`).
- Bibliotheken: Fehlt pydantic 2 (Python 3.12: Ubuntu 24.04 & Co.), bringt das Paket pydantic 2.14 (mit
  pydantic-core für cp312), typing-extensions, annotated-types, typing-inspection und websockets 17 unter
  `/usr/lib/kidctl/vendor/py3.12` mit (Liste mit SHA256 in `packaging/debian/vendor.txt`; die Startskripte
  nehmen den Ordner nur für die passende Python-Version in den Suchpfad). Neuere Systeme nutzen ihre Pakete
  (`Depends: python3-pydantic (>= 2) | python3 (<< 3.13)` usw.). Tray: PySide6, sonst PyQt6 aus den
  Systempaketen (`kidctl_tray/qt.py`).
- Verteilung: signiertes APT-Repository unter `<BASE_URL>/repo/deb` (Suite `stable`, Komponente `main`),
  gebaut auf dem Arch-Rechner ohne dpkg (`build-deb.py`, `apt-repo.py`), signiert mit dem Schlüssel des
  Pacman-Repos (Ed25519; apt prüft InRelease). `kidctl-repo-client.sh` erkennt Debian/Ubuntu: Schlüssel nach
  `/etc/apt/keyrings/kidctl.asc` (nach Fingerabdruck-Prüfung), `kidctl.sources` mit `Signed-By`. Der Hinweis
  „Agent veraltet“ liest auch `repo/deb/pool/main/kidctl_<ver>_*.deb`; Texte nennen „pacman -Syu bzw. apt
  upgrade“.
- Paketdatenbank im Agenten (`pkgdb.py`): pacman oder dpkg. dpkg: Abhängigkeiten aus Pre-Depends, Depends und
  Recommends (Metapakete ziehen den Desktop über Empfehlungen), bei Alternativen alle installierten,
  Pseudogruppe `essential` (grep, sed, coreutils … hängen von nichts ab), installiert = Status i/h mit i, t
  oder W (ausstehende Trigger während eines apt-Laufs, in dem der Agent neu startet – sonst fehlten z. B.
  die GLib-Programme bis zur nächsten Berechnung), Pfade unter `/bin` usw. auf `/usr/…` (usrmerge).
  Programmverzeichnisse zusätzlich `/usr/libexec`, `/usr/sbin`, `/usr/games`; ausführbare Dateien der Pakete
  auch unter `/usr/share` (apport, Ubuntus Migrationsskripte). Hilfsprogramme mit `PATH=/usr/bin:/usr/sbin`
  (`nft`, `unbound` liegen unter Debian in `/usr/sbin`).
- Login-Sperre: `pam-auth-update`-Profil `/usr/share/pam-configs/kidctl` (gleicher Block wie unter Arch,
  „Additional“ in `common-account`), Priorität 200 – vor `sss` (128), dessen `sufficient pam_localuser`
  die Prüfung für lokale User sonst vorher erfolgreich beendet (Ubuntu 24.04, im Test gefunden).
  Integrität prüft `common-account`. Entfernen per `pam-auth-update --remove` im prerm.
- DNS-Filter: Unit startet `/usr/sbin/unbound`; `AppArmorProfile=-unconfined`, weil Ubuntus unbound-Profil
  `/run/kidctl/dns` nicht lesen lässt (ohne AppArmor wirkungslos). Ubuntus Stub-Resolver (127.0.0.53) wird
  per nftables-Umleitung genauso umgangen wie unter Arch.
- noexec-Prüfung liest die Mounts aus Sicht des Systems (`/proc/1/mountinfo`): Im Namespace des Agenten kam
  unter Ubuntu das `noexec` eines Bind-Mounts (Spieleordner) nicht an.
- Snap wie Flatpak: Prozess in Cgroup `snap.<name>.…` **und** eigener Mount-Namespace **und** Programmdatei
  von einem Snap-Image (squashfs unter `/snap`, Geräte aus `/proc/1/mountinfo`) -> Schlüssel `snap:<name>`;
  eine selbst angelegte Unit gleichen Namens genügt nicht. Katalog aus `/var/lib/snapd/desktop/applications`
  (`X-SnapInstanceName`), Server akzeptiert `snap:`-Schlüssel. Firefox als Snap liest `/etc/firefox/policies`
  (DoH gesperrt, getestet). Das Paket legt Chromium- und Chrome-Policies an (`/etc/chromium`, `/etc/opt/chrome`).
- Basisliste Debian/Ubuntu (`packaging/debian/base-programs.txt`): Metapakete der Desktops (ubuntu-desktop,
  kubuntu-desktop, task-gnome-desktop …), `group:essential`, snapd, Flatpak, ibus-Engines. **Dateien
  (Nautilus) unter Ubuntu immer erlaubt**: Ubuntus Desktop-Symbole (Erweiterung „Desktop Icons NG“) starten
  es alle paar Sekunden per D-Bus – ohne Freigabe endlose Neustarts (die Erweiterung stürzt ab) und
  Sperrmeldungen.
- Dienste beim Login ohne Popup: Ubuntu aktiviert D-Bus-Dienste über `dbus-daemon` – dessen Prozesse
  zwischen Dienst und systemd-Benutzerdienst werden übersprungen.
- Steam aus Ubuntus `steam-installer` (`/usr/games/steam`): Bibliothek `~/.steam/debian-installation` ->
  `/srv/steam/<kind>` (sonst wie unter Arch); Freigabe-Schlüssel `exe:/usr/games/steam`. Heroic aus dem
  offiziellen .deb: `/opt/Heroic/heroic` gilt als Launcher.
- Logind trägt unter GDM keinen Desktop ein (auch Ubuntu) – Abmeldung über die laufende Shell (wie GNOME).

### Windows (entschieden 2026-11-04; Schritte 1–4 umgesetzt in 0.29.0–0.32.0, Nacharbeit bis 0.32.2)
Per /grill-me abgestimmt. Grundsatz: gleiches Verhalten wie unter Linux, wo Windows es zulässt;
Abweichungen stehen hier und unter „Restlücken“.

- **Zielsysteme:** Windows 11 ab 23H2 und Windows 10 22H2, nur x64 (kein ARM). Home und Pro mit derselben
  Technik (kein AppLocker, keine Gruppenrichtlinien-Editoren). Lokale und Microsoft-Konten; Zuordnung
  Benutzer → Kind über die **SID** (Anzeige: Kontoname bzw. E-Mail des Microsoft-Kontos).
- **Admin-Schutz:** Mitglieder der Gruppe Administratoren (`S-1-5-32-544`) werden nie eingeschränkt,
  auch wenn sie einem Kind zugeordnet sind (Grundregel wie `wheel`/`sudo`).
- **Aufbau (Weg 1):** Python wie unter Linux. Der plattformneutrale Kern (Regeln, Zählung, Spiele-Limit,
  Programmzeit, Chat-Warteschlange, Verbindung, Zustand, Sprache, `common/`) wird minimal-invasiv hinter
  eine Plattform-Schnittstelle gezogen (Sitzungen, Prozesse, Login-Sperre, Webfilter, Benachrichtigungen,
  Programmkatalog); Linux-Verhalten bleibt unverändert. Windows-Teile nur über `ctypes` (ohne `pywin32`):
  `agent/kidctl_agent/core.py` (Kern, `AgentCore`), `agent.py` (Linux), `win/` (Windows).
  Ausgeliefert mit dem offiziellen Embeddable-Python (von der PSF signiert) und mitgebrachten Bibliotheken,
  kein PyInstaller (Fehlalarme von Virenscannern). Native Teile (z. B. Credential Provider) höchstens als
  spätere, optionale Ausbaustufe.
- **Agent:** Windows-Dienst als `LocalSystem`, Geräte-Token nur für SYSTEM/Administratoren lesbar. Zusätzlich
  unter `HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal` und `\Network` eingetragen: Den abgesicherten
  Modus erreicht ein Standardnutzer vom Anmeldebildschirm aus. Dort läuft statt des Agenten ein **Wächter**
  (`win/safemode.py`, ohne `asyncio`, das ohne Netzwerk-Stack nicht ladbar ist): Er deaktiviert alle zugeordneten
  Kinderkonten aus der gespeicherten Policy und meldet ihre Sitzungen nach einem Hinweis ab. Kinder können im
  abgesicherten Modus (mit und ohne Netzwerk) also nicht arbeiten; Eltern und Admins sind nicht betroffen. Beim
  nächsten normalen Start stellt der Agent die Konten nach der Policy wieder her (entschieden 2026-11-08).
- **Benutzername auf dem Draht:** Windows-Kontoname, wenn er dem Linux-Muster entspricht (meist: „lisa“,
  „Max“), sonst `rid-<RID>`; `uid` = RID (lokale Konten ab 1000). Intern über die SID zugeordnet; voller Name
  bzw. bei Microsoft-Konten „Name (E-Mail)“ als Anzeige.
- **Zählung:** Sitzungen über die WTS-API im 5-s-Takt (Verbindungszustand `WTSActive`, Sperre aus
  `WTSSessionInfoEx.SessionFlags`); gezählt wird die aktive, entsperrte Sitzung zugeordneter Kinder. Dauer über monotone Uhr ohne Ruhezustand
  (`QueryUnbiasedInterruptTime`), Systemuhr nur für Tag/Zeitfenster. Zeitzone aus der Policy (Standardnutzer
  dürfen die Zeitzone von Windows ändern, die Uhr nicht).
- **Zeitende und „Jetzt sperren“:** Vorwarnungen und Countdown wie unter Linux, Abmeldung per
  `WTSLogoffSession`.
- **Login-Sperre:** Konto des Kindes deaktivieren (`UF_ACCOUNTDISABLE`), solange keine Zeit verfügbar ist –
  wirkt vor jeder Anmeldeart (Passwort, PIN, Windows Hello, Microsoft-Konto, abgesicherter Modus) und ohne
  laufenden Agenten. Windows zeigt nur den festen Text „Konto deaktiviert“.
  - **Nicht ausfallsicher wie PAM** (Sperre bleibt bei Absturz/Deinstallation bestehen), daher: Abgleich beim
    Start und alle 5 s aus der gecachten Policy; kidctl merkt sich, welche Konten es selbst deaktiviert hat,
    und fasst andere nie an; Deinstallation aktiviert diese Konten wieder; Admin-Konten nie.
  - Rückfall: Kommt trotz Sperre eine Sitzung eines gesperrten Kindes zustande, zeigt der Agent einen Hinweis
    und meldet sie nach etwa 10 s ab.
  - Windows blendet deaktivierte Konten auf dem Anmeldebildschirm ganz aus (kein Hinweis sichtbar) und merkt sich
    die Kontenliste. Ändert sich eine Sperre, startet der Agent den Anmeldebildschirm (`LogonUI.exe`) in Sitzungen
    ohne angemeldeten Benutzer neu – sonst erschiene ein freigegebenes Konto erst nach etwa einer Minute.
  - Später optional: eigener Credential Provider (C++) für einen Hinweis wie „wieder ab 14:00“.
- **Tray-App:** PySide6 wie unter Linux, Start für alle Benutzer über `HKLM\…\Run`. Verbindung zum Agenten
  über die Named Pipe `kidctl-tray` (nur interaktive Benutzer, `PIPE_REJECT_REMOTE_CLIENTS`); Identität über
  das Token des Client-Prozesses (SID) statt `SO_PEERCRED`.
  - Benachrichtigungen schickt der Agent als `TrayNotify` an die Tray-App (der Dienst hat keinen Desktop); ein
    Klick kommt als `TrayAction` zurück. Ohne verbundene Tray-App: kritische Meldungen als Meldungsfenster
    (`WTSSendMessage`).
  - Ist in einer aktiven Kinder-Sitzung 60 s lang keine Tray-App verbunden (Update hat sie beendet, Kind hat sie
    geschlossen), startet der Agent sie dort (`CreateProcessAsUser`), höchstens alle 5 min je Sitzung.
  - Countdown-Fenster aus dem Zustand (`TrayState.logout_in_seconds`, neu); die Tray-App zählt selbst herunter.
  - Toasts mit Knöpfen („Verlängerung anfordern“, „Freigabe anfragen“) über Startmenü-Verknüpfung mit
    AppUserModelID; Knöpfe öffnen `kidctl://…`, das die laufende Tray-App über ihre Pipe erreicht (kein
    COM-Server).
  - Toasts: Warnungen 15 und 5 min, Chat, Sperrmeldungen. **1-Minuten-Warnung und 60-s-Countdown
    zusätzlich als eigenes Fenster im Vordergrund** (mit „Verlängerung anfordern“) – „Nicht stören“ (vom Kind
    einschaltbar, bei Vollbildspielen automatisch) unterdrückt Toasts. Gleiches Muster beim Spiele-Limit.
- **Programmkontrolle (Schritt 2, umgesetzt in 0.30.0):**
  - Prozessliste alle 0,5 s (Toolhelp; Abweichung von „ETW“: ETW über ctypes wäre fehleranfällig, die Abfrage
    kostet < 1 ms), neue Prozesse sofort geprüft, vollständiger Abgleich alle 10 s und nach jeder Policy.
    Gesperrt = Prozess beenden, Toast „Programm gesperrt“ mit „Freigabe anfragen“, Sperrprotokoll. Modi Aus /
    Nur protokollieren / Sperren wie unter Linux; ohne gelesenen Katalog wird nur protokolliert.
  - **Schlüssel:** `exe:<Pfad>` (Groß-/Kleinschreibung egal, gemeldet in der Schreibweise des Katalogs) und
    `appx:<Package Family Name>` für Store-Apps (Server prüft das Format; eigene Programme auch als
    `C:\…\prog.exe`).
  - **Regeln** (`win/programs.py`), in dieser Reihenfolge: KidCTL selbst und Basisordner (WebView2-Laufzeit,
    `Common Files\microsoft shared`, Windows Defender) erlaubt; Store-Apps: freigegeben erlaubt, mit
    Startmenü-Eintrag Eltern-Entscheidung, unbekanntes Paket wie eine App (Katalog wird neu gelesen), ohne
    Eintrag (Hintergrund) erlaubt, Windows-Helfer aus `System32` mit App-Identität (`backgroundTaskHost`)
    erlaubt, Pakete `MicrosoftWindows.Client.*` (Suche, Geräteübergreifendes …) und die Einstellungen Basis; **Programmdateien,
    die dem Kind gehören, gesperrt** (auch in beschreibbaren Ordnern unter `C:\Windows`), außer im App-Ordner
    einer freigegebenen App; unter `%WINDIR%`: `cmd` nur mit `/c` (interaktiv gesperrt), Systemwerkzeuge
    (PowerShell, Skript-Hosts, `reg`/`regedit`, `mmc`, `msiexec`, Task-Manager, Systemsteuerung, `schtasks`,
    `netsh`, `wmic` … siehe `SYSTEM_TOOLS`) nur mit Freigabe, `rundll32`/`regsvr32` mit DLL außerhalb von
    Windows/Program Files gesperrt (nicht anfragbar), Programme mit Startmenü-Eintrag Eltern-Entscheidung
    (außer Explorer; erlaubt, wenn die Aufgabenplanung bzw. ein Dienst sie startet), sonst Basis; freigegebene
    Programme samt Programmordner erlaubt (unter AppData der App-Ordner, z. B. `…\AppData\Local\Roblox` –
    übersteht Updates in neue Versionsordner); alles andere gesperrt.
  - **Wechseldatenträger** (ab 0.48, Linux und Windows): Freigaben nach Pfad und Programmordner gelten auf
    Wechseldatenträgern nicht, Programme und Skripte darauf sind gesperrt und nicht anfragbar (Code
    `removable_media`), weil das Kind den Inhalt austauschen kann. Erkennung Linux: udisks2-Einhängepunkte
    `/run/media/`, `/media/`. Windows: Laufwerkstyp Wechselmedium/DVD oder Bus USB, 1394, SD, MMC, Abbild
    (`IOCTL_STORAGE_QUERY_PROPERTY`), nie das Systemlaufwerk, Ergebnis 10 s je Laufwerksbuchstabe zwischengespeichert.
    Weiter erlaubt: Store-Apps und Spieleordner freigegebener Launcher (Steam-Bibliothek auf USB-Platte). Der Server
    lehnt eigene Programme unter `/run/media/` und `/media/` ab. Restlücke in `docs/limitations*`.
  - **Ausführverbot statt `noexec`** (`win/acl.py`, per `icacls`, Zustand `winacl.json`; nur im Modus „Sperren“): auf dem Profil des
    Kindes sowie `C:\Users\Public` und `%WINDIR%\Temp`: `Deny Kind (OI)(IO)(X)` (Dateien nicht ausführbar,
    keine DLLs daraus – gegen COM-Hijacking), `Deny Kind (OI)(CI)(WDAC)` und `OWNER RIGHTS (OI)(CI)(M)` (Kind kann
    Rechte nicht ändern). Freigegebene Apps im Profil: `Allow Kind (OI)(IO)(RX)` auf dem App-Ordner (vererbte
    Einträge des näheren Ordners gehen vor). Aufheben stellt den Vollzugriff des Kindes auf sein Profil wieder
    her (`icacls /deny` streicht WDAC auch aus dessen Erlaubt-Eintrag). Integrität `acl:<user>` (aus SDDL,
    sprachunabhängig) mit Selbstreparatur; Deinstallation (`unlock-all`) nimmt alles zurück.
  - **Katalog** (`win/catalog.py`, PowerShell als SYSTEM, alle 6 h und bei unbekanntem Paket): Startmenü-
    Verknüpfungen (systemweit und je Profil, ohne Admin-Profile und KidCTL) mit übersetztem Anzeigenamen,
    Shells als „<name> (Shell)“, Store-Apps mit sichtbarem Eintrag. Store-Namen löst Windows für SYSTEM nicht
    auf – feste Namen für die mitgelieferten Microsoft-Apps (`KNOWN_APPS`, deutsch/englisch), sonst lesbarer
    Paketname. Kategorien über Schlüsselwörter, Programme unter `%WINDIR%` als „System“.
- **Website-Filter (Profil folgt der aktiven Sitzung; Schritt 3, umgesetzt in 0.31.0):** Unter Windows löst der
  Systemdienst `dnscache` für alle Programme auf – ein Filter je Benutzer auf DNS-Ebene ist nicht möglich.
  - Lokaler Unbound (offizieller Windows-Build 1.26.2, `<Installation>\unbound`, Kindprozess des Agenten) auf
    `127.0.0.1:53`/`::1:53`, Konfiguration unter `ProgramData\KidCTL\dns`. Zonen, SafeSearch, DoH-Sperren,
    Sperrlisten und Sperrprotokoll wie unter Linux (`dnsfilter.build_zones` usw.). Profil: Kind, dessen Sitzung
    aktiv ist; Eltern/Admin aktiv oder niemand = ungefiltert (ohne Zonen). Bei jedem Wechsel Neustart und
    DNS-Cache von Windows leeren (`DnsFlushResolverCache`). Verwaiste Resolver beendet der Agent beim Start.
  - Weiterleitung an die ursprünglichen DNS-Server der Adapter (Router; lokale Namen wie `fritz.box`) mit
    `forward-first` (sonst eigene Rekursion); Suchdomänen eines Adapters (Firmen-VPN) an dessen DNS-Server.
    Ein fester Upstream aus der Policy ist nicht umgesetzt.
  - **Adapter:** Alle Schnittstellen zeigen auf `127.0.0.1`/`::1` (PowerShell `Set-DnsClientServerAddress`);
    die ursprünglichen Einstellungen (statisch bzw. DHCP) merkt sich der Agent (`windns.json`). Prüfung alle 30 s
    (Registry, z. B. neues VPN). Zurückgesetzt, wenn kein Kind mehr einen Filter hat, wenn ein Admin den Dienst
    stoppt (sonst hätte niemand DNS) und bei der Deinstallation (`unlock-all`).
  - **WFP** (`win/wfp.py`, statt Windows-Firewall: deren Regeln mit „Lokaler Benutzer“ sperren ausgehend nicht –
    in der VM geprüft): Filter auf `ALE_AUTH_CONNECT` V4/V6 mit `ALE_USER_ID` = Kind; Port 53 und 853 für Kinder
    mit Website-Filter (eigene Resolver in Apps, DoT/DoQ). Abweichung von der Planung: keine rechnerweite Sperre
    von Port 53 – die Adapter zeigen ohnehin auf Unbound. Dynamische WFP-Sitzung: Endet der Agent, entfernt
    Windows die Filter (nie verwaiste Sperren; der Dienst startet neu).
  - **Netzsperre für Kinder-Sitzungen im Hintergrund:** WFP sperrt alle neuen Verbindungen der SID eines Kindes
    mit Website-Filter, solange es eine Sitzung hat, die nicht die aktive ist.
  - DoH: Sperrliste wie unter Linux; Richtlinien des Installers: Edge und Chrome `DnsOverHttpsMode=off`, Firefox
    `DNSOverHTTPS` aus und gesperrt, Windows `DoHPolicy=1` (verboten). Integrität `dns` (Resolver läuft, Adapter
    umgestellt) und `browser` (Richtlinien), nur wenn ein Kind den Filter nutzt.
  - Gesperrte Anfragen: Sperrprotokoll und „Freigabe anfragen“ (Hauptdomain) wie unter Linux.
- **Spiele (Schritt 4, umgesetzt in 0.32.0):**
  - Steam: Erkennung über den Installationsordner (Abweichung: Umgebungsvariablen wie `SteamAppId` fremder
    Prozesse sind unter Windows nicht ohne Weiteres lesbar) – Bibliotheken aus `steamapps\libraryfolders.vdf`,
    Spiele aus `appmanifest_*.acf` (`installdir`) -> `game:steam:<appid>` mit Namen. Steam-Ordner aus der Registry.
  - Epic Games: Manifeste `ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item` (`InstallLocation`,
    `AppName`, `DisplayName`) -> `game:epic:<AppName>`.
  - Store-/Xbox-Spiele: Katalog (Kategorie „Spiele“ bei `MicrosoftGame.config`). Launcher (Steam, Xbox-App, Game Bar
    …) zählen nicht als Spiel; die Game Bar (startet mit jedem Spiel), die Gaming Services (`Microsoft.GamingServices`,
    von Store-Spielen wie Minecraft gebraucht) und Microsoft GameInput (`Program Files\Microsoft GameInput`, Eingabe in
    Spielen wie Minecraft Bedrock) gehören zur Basis. Programme unter `WindowsApps` ohne App-Identität
    werden ihrem Paket zugeordnet (Ordnername -> Package Family Name) und wie Store-Apps geprüft.
  - **Launcher-Regel** (`win/games.LAUNCHERS`: Steam, Epic, Battle.net, EA App, Ubisoft Connect, GOG Galaxy, Riot):
    Ist der Launcher freigegeben, sind von ihm gestartete Prozesse erlaubt; bei Steam und Epic zusätzlich ihre
    Spieleordner – auch wenn die Dateien dem Kind gehören (Steam lädt unter dem Konto des Kindes).
  - Steam-Bibliothek (vom Kind beschreibbar) bekommt ohne Steam-Freigabe das Ausführverbot (ACL, wie Profil).
  - **Programmzeit, Spielzeit, „spielt gerade“** (`win/apptime.py`): je Prozess erkanntes Spiel, Store-App oder
    Katalog-Programm (samt Programm-/App-Ordner); Spielzeit, wenn mindestens ein Spiel läuft. Spiele-Limit wie
    unter Linux (`gamelimit.GameLimit`: Vorwarnung 5/1 min, danach Spielprozesse beenden, auch neu gestartete).
  - Tray: letzte Minute Spielzeit als Fenster im Vordergrund mit „Mehr Spielzeit anfragen“ (`TrayState.gaming`).
  - **Neu, für alle Plattformen:** Schalter „zählt als Spiel“ je Kind und App in der Programmliste (Tabelle
    `child_game_app`, `ChildPolicy.game_apps`); Apps der Kategorie „Spiele“ sind fest markiert. Der Agent (Linux
    und Windows) prüft Spiele je Kind (`AgentCore.is_game`). Markierte Apps (auch eigene Programme) zeigt die
    Programmliste in der Gruppe „Spiele“ (ab 0.45.1; nicht mehr installierte bleiben unter „Nicht mehr installiert“).
- **Spielerkennung erweitert (entschieden 2026-11-19 per /grill-me):**
  - **Auslöser:** Ein über Steam gestartetes Spiel wurde unter Windows nicht als Spiel erkannt (keine Spielzeit,
    kein Spiele-Limit). Ursache zuerst in der VM nachstellen und beheben (Kandidaten: Index nur beim
    Katalog-Durchlauf neu eingelesen; Steam nur unter `HKCU` registriert; Spiel-`.exe` außerhalb von `installdir`).
  - **Manifeste je Launcher** für Namen und stabile Schlüssel `game:<launcher>:<id>`: Steam, Epic (bisher), neu
    **EA App**, **Ubisoft Connect**, **Battle.net**, **Wargaming Game Center**, **GOG Galaxy**. Formate aus echten
    Installationen ermitteln (Registry/ProgramData), robust parsen, Fehler nur loggen.
  - **Prozess-Abstammung als Rückfall:** Ein Prozess, der direkt oder indirekt von einem Launcher aus `LAUNCHERS`
    gestartet wurde, zählt als Spiel – ausgenommen eine feste Liste von Hilfsprozessen je Launcher (z. B.
    `steamwebhelper.exe`, Crash-Reporter, Updater, Overlays). Schlüssel `game:exe:<normierter Pfad>`, Name aus den
    Versionsinformationen der `.exe` (ProductName/FileDescription), sonst Dateiname. Die Abstammung bleibt erhalten,
    wenn der Launcher sich danach beendet (Elternkette beim Prozessstart merken).
  - **Index sofort aktualisieren:** Änderungen an Manifest-Ordnern bzw. Bibliotheken (Prüfung im Takt oder
    Verzeichnisüberwachung) lösen ein Neueinlesen aus, nicht erst der Katalog-Durchlauf.
  - `LAUNCHERS` um **Wargaming Game Center** ergänzen (Freigabe-Regel wie bei den anderen Launchern).
  - Launcher selbst zählen weiterhin nicht als Spiel.
  - **Restlücke** (`docs/limitations*.md`): Ein Spiel, das ohne Launcher und ohne bekanntes Manifest direkt über die
    `.exe` gestartet wird, zählt nur über den Schalter „zählt als Spiel“ in der Programmliste.
  - **Nur Windows.** Linux: Lutris/Bottles (Wine) siehe „Spielzeit je Spiel“ (Roadmap `wine-launchers`, 2026-11-23).
  - **Umgesetzt (0.36.0):** Ursache des Steam-Fehlers: Der Index wurde nur alle 6 h mit dem Katalog gelesen, und
    Steam wurde nur über `HKLM` gefunden. Jetzt gibt es eine Prüfung alle 10 s (Änderungszeit der Manifest-Ordner bzw.
    Registry-Schlüssel), Steam auch über `HKEY_USERS\<SID>`, und ein unbekannter Prozess in einer Bibliothek löst ein
    Neueinlesen aus. Manifeste nach den echten Installationen im VM-Snapshot „launchers“ (Testdaten
    `agent/tests/fixtures/win_launchers`):
    - GOG: Registry `GOG.com\Games\<id>`.
    - EA: Uninstall-Eintrag mit `__Installer\installerdata.xml`.
    - Battle.net: `product.db` (Protobuf).
    - Ubisoft Connect und Wargaming: Spiele zu groß für die VM, deshalb kein Format; nur Rückfall (Restlücke).
      Ubisoft nachgezogen (2026-11-22, Daten vom Rechner eines Nutzers): Registry
      `Ubisoft\Launcher\Installs\<id>` (`InstallDir`) -> `game:ubisoft:<id>`, Name = Ordnername.

    Steamworks-Redistributables zählen nicht als Spiel. Vom Rückfall ausgenommen sind Programme im Ordner eines
    laufenden Launchers (z. B. `EALocalHostSvc.exe`). Kurze 8.3-Pfade (`PROGRA~1`) werden vor der Zuordnung in die
    lange Form aufgelöst. Freigegebene Launcher GOG, EA und Battle.net geben auch ihre Spieleordner frei, wie bisher
    Epic.
  - **Startmenü-Einträge der Launcher (Fehler 2026-11-22):** Freigegeben wird der Startmenü-Eintrag, und der zeigt
    bei drei Launchern auf einen Starter neben dem eigentlichen Launcher (echte Installationen, VM-Snapshot
    „launchers“): `Battle.net Launcher.exe` -> `Battle.net.exe`, `EALauncher.exe` -> `EADesktop.exe`,
    `UbisoftConnect.exe` -> `upc.exe` (`win/games.LAUNCHER_ENTRIES`). Ein freigegebener Eintrag gibt den Launcher
    samt Spieleordnern frei; ein Launcher aus `LAUNCHERS` im Programmordner einer Freigabe zählt für die
    Abstammungsregel. Battle.net gibt zusätzlich `ProgramData\Battle.net` frei (Agent in versionierten Ordnern
    `Agent\Agent.NNNN`, `win/games.LAUNCHER_DATA_DIRS`) – keine Einzelfreigaben je `.exe`, auch nicht nach Updates.
    Starter der Spiele (z. B. `Hearthstone Beta Launcher.exe`) liegen im Spieleordner und zählen zum Spiel.
    Restlücke: `docs/limitations*.md` („Ordner freigegebener Launcher“).
- **Oberfläche im Windows-11-Stil (entschieden 2026-11-19 per /grill-me):**
  - **Nur unter Windows** (10 und 11 gleich); Linux behält den nativen Look (Breeze/Adwaita). Logik, Texte und
    Übersetzungen bleiben gemeinsam; das Windows-Design ist eine eigene Stilschicht (`tray/kidctl_tray/winstyle.py`
    + QSS), geladen nur unter Windows.
  - **Fluent-Gestaltung per Qt-Stylesheet:** Segoe UI Variable (Rückfall Segoe UI), großzügige Abstände, Ecken 8 px
    (Fenster/Karten) bzw. 4 px (Eingaben/Buttons), primärer Button gefüllt in Akzentfarbe, sekundäre Buttons
    umrandet, Eingabefelder mit Akzent-Unterstrich im Fokus, hell/dunkel nach Windows-Einstellung (live
    umschaltend). **Mica** als Fensterhintergrund unter Windows 11 (DWM `DWMWA_SYSTEMBACKDROP_TYPE`), Windows 10
    einfarbig. Fenster werden dafür neu aufgebaut (keine reinen Formular-Layouts mehr), Funktion unverändert.
  - **Akzentfarbe:** Windows-Akzentfarbe des Benutzers; Kontrastprüfung (Text auf Akzent ≥ 4,5:1), sonst
    KidCTL-Blau `#3b5ba9`. **Statusfarben fest** (ok/Warnung/kritisch/offline) in Fluent-Tönen, je hell/dunkel;
    harte Farben wie `#da4453` entfallen.
  - **Umfang:** alle Fenster der Tray-App (Countdown, Zeit anfragen, Freigabe anfragen, Gesperrt-Fenster, Chat),
    **Tray-Menü** (Windows-11-Optik), **Tray-Symbol** (monochromes KidCTL-Symbol passend zur Taskleiste hell/dunkel
    mit kleinem Statuspunkt; Restzeit im Tooltip und im Menü – nur Windows, Linux behält Kreis mit Zahl),
    **Toasts** (App-Logo, App-Name „KidCTL“, Aktionsbuttons im Windows-Standard), **Installer** (Inno Setup:
    moderner Assistent-Stil, KidCTL-Logo, Kopplungsseite aufgeräumt: Server und Code untereinander, kurzer Hinweis).
  - Kinder-Clients bleiben immer „KidCTL“ (kein Whitelabel).
  - **Prüfung:** `dev/winshots.py` öffnet in der Windows-VM jedes Fenster in Beispielzuständen (Countdown 5 min,
    Anfragen, Gesperrt, Chat mit Verlauf, Tray-Menü, Toast, Installer-Kopplungsseite) und fotografiert Windows 11
    hell/dunkel und Windows 10 hell, jeweils DE/EN. Die Bilder ersetzen die bisher von Hand geplanten
    Client-Screenshots für Projektseite und Wiki. Offscreen-Tests: Kontrastprüfung/Farbauswahl, Stylesheet nur unter
    Windows, alle Fenster lassen sich in beiden Schemata erzeugen.
  - **Umgesetzt (0.38.0):** Bausteine in `winstyle.py` (`window`, `title`, `form`, `card`, `primary`; unter Linux
    das bisherige Layout), Qt-Palette aus denselben Farbwerten. Radio-/Kontrollkästchen und Zahlen-/Zeitfelder bleiben
    beim nativen Qt-Stil „windows11“ (Qt ≥ 6.7), weil ein Stylesheet dessen Fluent-Zeichnung abschaltet. **Mica nur
    im Fensterrahmen** (Titelleiste); die Fläche hat die passende Fluent-Grundfarbe, denn ein durchsichtiger Inhalt ist
    mit Qt-Widgets unter Windows fehleranfällig. Tray-Menü mit runden Ecken über DWM. Toast-Logo als PNG im Profil
    (`%LOCALAPPDATA%\KidCTL\toast-logo.png`). Installer: `WizardStyle=modern dynamic`, Logo, Kopplungsseite mit
    kurzem Hinweis. `dev/winshots.py`: Fenster per `grab()` in der VM (eingebettetes Python, Segoe-Schrift), dazu
    Desktop-Abzüge über QMP (Toast, Installer). Windows-10-Screenshots: noch offen.
- **Integrität (WebUI):** Dienst, SafeBoot-Eintrag, Unbound, WFP-Filter, DNS der Adapter, Ausführverbote
  (ACL), Kinder nicht in der Gruppe Administratoren.
- **Verteilung und Updates:**
  - Installer mit Inno Setup (gebaut in der Windows-VM), zunächst **ohne Code-Signatur**: beim ersten
    Installieren einmal SmartScreen bestätigen. Code-Signing-Zertifikat später bei Bedarf (Azure Artifact
    Signing gibt es für Privatpersonen nur in USA/Kanada; Let's Encrypt kann kein Code signieren). Keine
    Ausnahmen in Defender.
  - Server liefert Installer und Updates als statisches Verzeichnis (wie die Repos), z. B. `/windows/`.
  - **Automatisches Selbst-Update** des Agenten, sobald der Server eine neue Version anbietet; vorher Prüfung
    einer eigenen **Ed25519-Signatur** (öffentlicher Schlüssel im Agenten). Version je Gerät im WebUI
    (Hinweis „Agent veraltet“ wie bisher).
  - Kopplung mit Code wie bisher (`kidctl enroll` bzw. Feld im Installer); Deinstallation nimmt alles zurück
    (Konten, ACLs, WFP, DNS der Adapter, SafeBoot, Browser-Richtlinien).
  - Server: Plattform je Gerät, Programmschlüssel für Windows-Pfade (Groß-/Kleinschreibung egal) und
    Store-Apps.
- **Härtung:** `docs/hardening.de.md` um Windows ergänzen (BitLocker, BIOS-Passwort, kein Booten von USB, Kinder
  nie Administratoren, UAC mit Admin-Passwort).
- **Testumgebung:** `dev/vm/vm.sh` mit Windows-Zielen (OVMF mit Secure Boot, `swtpm`, virtio-Treiber,
  `autounattend.xml`: lokaler Admin, zwei Kinderkonten, OpenSSH-Server; unaktiviert). VMs: Windows 11 Home
  25H2 (Hauptziel) und Windows 10 Pro 22H2. ISOs vom Nutzer unter `~/.local/share/kidctl-vm-win11/install.iso`
  bzw. `…-win10/install.iso`.
- **Umsetzung in Schritten** (je lauffähig, getestet, eigener Changelog-Eintrag):
  1. Grundgerüst und Bildschirmzeit: Plattform-Schnittstelle (Linux-Tests grün), Dienst, Kopplung, Zählung,
     Abmeldung, Login-Sperre, „Jetzt sperren“, Tray mit Chat/Toasts/Vordergrund-Fenster, Installer,
     Selbst-Update, SafeBoot, Integrität, Test-VMs, Server-Anpassungen.
  2. Programmkontrolle: Prozessliste, Katalog (`.lnk`, AppX), Basisliste, Ausführverbote mit Ausnahmen,
     Freigabe-Anfragen – umgesetzt in 0.30.0.
  3. Website-Filter: Unbound, WFP, DNS der Adapter, Profilwechsel, Hintergrund-Netzsperre, Browser-Richtlinien –
     umgesetzt in 0.31.0.
  4. Spiele: Steam, Epic, Xbox/Store, Schalter „zählt als Spiel“, Spiele-Limit, Spielzeit je Spiel –
     umgesetzt in 0.32.0.

#### Restlücken – Hintergrund (Windows)
(Elternfassung in `docs/limitations.de.md`; entschieden 2026-11-04 bzw. 2026-11-07, hierher verschoben 2026-11-17.)
- Whitelist-Modus: Windows-Infrastruktur (Update, Defender, SmartScreen, Konnektivitätstest, Zeit, Zertifikate,
  Lizenzen) fest erlaubt; Hintergrundrauschen (Telemetrie, MSN/Widgets, Wetter-Kacheln, Edge-Konfiguration) gesperrt
  und nicht im Sperrprotokoll (feste Listen in `win/dnsfilter.py`). Bing bleibt sichtbar (Standardsuche in Edge,
  Freigabe muss anfragbar sein).
- Login-Sperre ohne eigenen Hinweis: Ein eigener Hinweis bräuchte einen Credential Provider.
- Named Pipe: muss interaktiven Benutzern Schreibrecht geben (Qt öffnet mit `GENERIC_WRITE`, das schließt das Anlegen
  weiterer Pipe-Instanzen ein) – ein Kind könnte eine zweite Instanz anlegen und Tray-Apps anderer Benutzer falsche
  Zustände zeigen; Durchsetzung und Identität beim Agenten nicht betroffen.
- Hintergrund-Netzsperre (WFP) gilt nur für neue Verbindungen; nach dem Hinzufügen eines Adapters kann `dnscache` bis
  zu 30 s dessen DNS-Server nutzen; während ein Kind vorne ist, lösen auch Dienste anderer Benutzer über `dnscache`
  mit seinem Filter auf; Weiterleitung der Suchdomänen (Firmen-VPN der Eltern) über den lokalen Resolver. Beim Start
  beendet der Agent jeden laufenden `unbound.exe`.
- Programmkontrolle: Prozessliste alle 0,5 s, sehr kurzlebige Prozesse (z. B. `reg.exe` aus `cmd /c`) können
  durchrutschen; `cmd /c …` ist erlaubt. DLLs im App-Ordner einer freigegebenen Profil-App; vom Ausführverbot schon
  beim Start abgewiesene Programme sieht der Agent nicht (kein Toast, keine Anfrage); weitere beschreibbare Ordner
  (z. B. `C:\ProgramData`) ohne Ausführverbot – Programme des Kindes darin gesperrt, DLLs nicht.
- Store-App-Namen der mitgelieferten Microsoft-Apps aus einer festen Liste (Windows löst sie für SYSTEM nicht auf).
- Vorwarn-Fenster nicht über exklusivem Vollbild; ohne Code-Signatur SmartScreen/Defender-Fehlalarme.

### Offline-Verhalten
- Letzte bekannte Policy wird weiter durchgesetzt.
- Nutzungszeit wird lokal gezählt und beim nächsten Kontakt synchronisiert.
- Nachrichten und Verlängerungsanfragen werden in eine Warteschlange gestellt.
- Offline sind keine neuen Genehmigungen möglich.

## Sprache / Internationalisierung (entschieden 2026-10-21)

- Deutsch und Englisch für alles, was Eltern und Kinder sehen: WebUI, E-Mails, Push, Benachrichtigungen
  des Agenten, Tray-App, Sperrtext beim Login. Logs (Journal), CLI und Doku bleiben deutsch; kurze
  englische `README.md` (deutsche Fassung `README.de.md`).
- Regel überall: `de*` -> Deutsch, alles andere (auch leer/`C`/`fr_FR`) -> Englisch.
- Eltern: Profil-Einstellung „Automatisch (Browser)“ (Standard) / Deutsch / English. Vorrang im WebUI:
  feste Profilsprache > Umschalter „Deutsch · English“ auf Seiten ohne Anmeldung (Cookie) >
  `Accept-Language` (höchster q-Wert). Mails und Push: feste Sprache, sonst die bei der letzten
  Anmeldung erkannte (`parent.detected_language`), sonst Deutsch. „Passwort vergessen“ nimmt die
  Sprache des Accounts, nicht die des anfragenden Browsers. Push wird je Elternteil in dessen Sprache
  erzeugt.
- Kind: Sprache seiner Desktop-Sitzung (Umgebung von plasmashell, gnome-shell u. a., LC_ALL > LC_MESSAGES >
  LANGUAGE > LANG, 60 s zwischengespeichert); ohne Sitzung die GNOME-Kontosprache (AccountsService), sonst
  (Login-Sperrtext) `/etc/locale.conf`. Die Tray-App nimmt
  die Sprache ihrer eigenen Umgebung.
- Sperrprotokoll: Der Agent schickt zusätzlich einen sprachneutralen Code mit Parameter
  (`blocked_event.detail_code/detail_arg`); der Server übersetzt je Elternteil. Ältere Einträge/Agenten:
  deutscher Text. Programmnamen: Agent meldet zusätzlich `Name=` (`app_entry.name_en`); Eltern und Kind
  sehen den Namen ihrer Sprache. Integritätsprüfungen: Bezeichnung aus der Prüfkennung übersetzt, Details
  bleiben deutsch.
- Formate Englisch: „Tue 20 Oct 2026, 14:03“ (24 h), Dauer „1 h 20 min“, „+25%“. Deutsch unverändert.
  Zeitzone bleibt die des Servers.
- Technik: gettext-artige `.po`-Kataloge ohne Abhängigkeit (`kidctl_common/i18n.py`, eigener Leser),
  Deutsch als Ausgangssprache im Code (`_()`, `ngettext()`, `N_()` für Konstanten), je Bestandteil
  `locale/en.po`. Tests je Bestandteil: jeder markierte Text hat eine englische Übersetzung mit gleichen
  Platzhaltern, keine verwaisten Übersetzungen. Begriffe: Screen time, Daily limit, Allowed hours,
  Weekly schedule, Add time / Bonus time, Request for more time, Game time / Game limit, Lock now,
  Access request / Ask for access, Always allow / Today only, App control (Off / Monitor only / Block),
  Web filter (Allow list / Block list), Activity log, Pair device, Usage report.

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
- Zeitfenster (Rahmen) innerhalb eines Kalendertags (Beginn < Ende) oder über Mitternacht (siehe „Sperrzeiten und
  Fenster über Mitternacht“).
- Nutzung endet, wenn Kontingent aufgebraucht **oder** Zeitfenster geschlossen ist **oder** eine Sperrzeit beginnt.
- Tageswechsel um 00:00 Europe/Berlin.

### Sperrzeiten und Fenster über Mitternacht (entschieden 2026-11-23 per /grill-me)
- Modell „Rahmen + Sperrzeiten“: Das bisherige Zeitfenster je Wochentag bleibt der Rahmen; dazu je Tag bis zu
  **3 Sperrzeiten** (z. B. Schule 08:00–13:00, Abendessen 18:00–19:00) mit optionalem Namen (≤ 40 Zeichen,
  verschlüsselt gespeichert). Sperrzeit: Beginn < Ende, nicht über Mitternacht; außerhalb des Rahmens ohne Wirkung.
- Gilt je Vorlage: Wochenvorlage und (mit Ferien/Feiertagen) Ferienvorlage haben eigene Sperrzeiten; Feiertage nutzen
  die Regel des eingestellten Wochentags samt Sperrzeiten.
- **Fenster über Mitternacht:** Ende < Beginn bedeutet „Ende am Folgetag“, spätestens 06:00. Nach 0 Uhr gilt der
  Rahmen des Vortags weiter (Sperrzeiten des Vortags wirken nach 0 Uhr nicht mehr); gezählt wird aber schon aufs
  Kontingent des neuen Tages (Tageswechsel für Zählung und Statistik bleibt 00:00). Erlaubt ist, was im Rahmen des
  Vortags (Teil nach 0 Uhr) **oder** im Rahmen des Tages außerhalb seiner Sperrzeiten liegt.
- Sperrzeit wirkt wie Fensterende: Vorwarnungen, 60-s-Countdown, Abmeldung, Login-Sperre per PAM. Die Restzeit ist
  das Minimum aus Kontingent und Zeit bis zur nächsten Sperrzeit bzw. zum Rahmenende. Tray zeigt während einer
  Sperrzeit „Sperrzeit „Schule“ bis 13:00 Uhr“ (Grund `blocked`).
- Gutschriften heben Sperrzeiten nicht auf (Aufheben nur über „Jetzt sperren/Freigeben“ oder die Vorlage). Ein
  Fensterende per Gutschrift darf ebenfalls über Mitternacht liegen (bis 06:00) und muss nach dem regulären Ende
  liegen.
- Protokoll 2 (`DayRule.blocks`, Ende < Beginn erlaubt); der Server akzeptiert weiter Protokoll 1. Geräte mit
  Protokoll 1 erhalten eine abgespeckte Policy: ohne Sperrzeiten, Ende über Mitternacht auf 23:59 gekappt (auch bei
  Tagesausnahmen). Die Kind-Seite im WebUI weist darauf hin, solange ein Gerät des Kindes Protokoll 1 spricht und
  Sperrzeiten oder Fenster über Mitternacht eingestellt sind.
- Offline wie bisher mit gecachter Policy; Fenster nach Systemuhr (Zeitzone der Policy), Dauer über monotone Uhr.

### Spiele-Limit (entschieden 2026-10-20)
- Je Wochentag optional „davon Spiele“: höchstens so viele Minuten Spiele am Tag, innerhalb der
  Bildschirmzeit (leer = kein eigenes Limit). Ein Gesamtlimit für alle Spiele, kein Limit je Spiel.
- Als Spiel zählen erkannte Steam-/Heroic-Spiele (`game:`-Schlüssel) und Katalog-Apps der
  freedesktop-Kategorie `Game` (Gruppe „Spiele“) – nicht die Launcher selbst (Steam, Heroic, Lutris).
- Spielzeit = Zeit, in der während gezählter Bildschirmzeit mindestens ein Spiel läuft (parallele
  Spiele zählen einmal). Geräteübergreifend wie die Bildschirmzeit (`usage_report.game_seconds`,
  `usage_update.others_game_seconds`, Maximum-Regel).
- Ablauf: Vorwarnung 5 und 1 min vorher (nur bei laufendem Spiel, mit Knopf „Mehr Spielzeit
  anfragen“), danach werden Spielprozesse geschlossen (SIGTERM, nach 10 s SIGKILL) und bis Tagesende
  jeder neu gestartete Spielprozess ebenso (Prüfung im 5-s-Takt). Der restliche Rechner bleibt nutzbar;
  ist die Bildschirmzeit vorbei, gilt der normale Ablauf (Abmeldung). Wirkt unabhängig von der
  Programmkontrolle.
- Mehr Spielzeit: Gutschrift „Spielzeit“ je Datum im WebUI (`day_exception.extra_game_minutes`) oder
  Anfrage aus dem Verlängerungsdialog der Tray-App (Wahl Bildschirmzeit/Spielzeit, nur Minuten;
  `extension_request.target="games"`), Genehmigung im Chat.

### Zählung
- Gezählt wird nur eine aktive, entsperrte Session: logind `Active=yes` und `LockedHint=no`.
  Nur Sitzungen der Klasse `user` (SDDM, GDM, TTY, SSH); Greeter, `manager` und `background`
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
  Tagesausnahmen ab gestern. Die Wochenregeln sind seit 2026-11-23 die geltenden Regeln der nächsten 7 Tage
  (Ferien/Feiertage eingerechnet, siehe „Ferien und Feiertage“).
- `version` = Hash des Inhalts; Agent bestätigt per `policy_ack`, WebUI zeigt je Gerät
  „aktuell / veraltet / noch nicht übertragen“.

### Ablauf bei Zeitende
- Vorwarnungen bei 15, 5 und 1 Minute(n) als Desktop-Benachrichtigung mit Knopf
  „Verlängerung anfordern“.
- Nach Ablauf 60 Sekunden Countdown zum Speichern, dann Abmeldung:
  - Grafische Sitzungen über den Desktop: Plasma `org.kde.Shutdown.logout`, GNOME
    `org.gnome.SessionManager.Logout(1)` (je ohne Rückfrage; siehe „Desktop-Umgebungen“).
    `TerminateSession` würde den Sitzungsleiter `sddm-helper` mitbeenden – SDDM wertet das als
    Absturz und zeigt keinen Anmeldebildschirm mehr (im VM-Test beobachtet).
  - Wirkt die Plasma-Abmeldung nach 20 s nicht: hart per `TerminateSession`; zeigt seat0 danach
    weder Greeter noch andere Sitzung, wird `display-manager.service` neu gestartet.
  - TTY-/SSH-Sitzungen direkt per `TerminateSession`.
- Login-Sperre per PAM (gilt für SDDM, GDM und TTY), solange kein Kontingent/Zeitfenster verfügbar.
  - Agent schreibt alle 5 s `/run/kidctl/locked-users` (zugeordnete User ohne Zeit, root 0644)
    und `/run/kidctl/locked-message`; sofort beim Start aus der gecachten Policy.
  - PAM: markierter Block am Ende des Account-Teils von `/etc/pam.d/system-login`
    (`pam_listfile` sense=deny `onerr=succeed`, Hinweis per `pam_echo` bzw. für GDM `pam_nologin`,
    `pam_deny`). Greift für SDDM, SDDM-Autologin, GDM, TTY (`login`) und SSH; nicht für die
    Plasma-Bildschirmsperre (`kde`) und `systemd-user`.
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

### Ferien und Feiertage (entschieden 2026-11-23 per /grill-me)
- **Ferienzeiträume je Familie** (Seite „Einstellungen“, Karte „Ferien & Feiertage“, alle Eltern): Name (optional),
  erster und letzter Tag (einschließlich), höchstens 366 Tage lang. Überschneidungen sind erlaubt. Angezeigt werden
  laufende und künftige Zeiträume; vergangene bleiben gespeichert, bis sie entfernt werden. Kein Schulferien-Import,
  nur manuelle Eingabe.
- **Ferienvorlage je Kind** (Tab „Zeiten“, zweite Karte): gleiche Felder wie die Wochenvorlage (Kontingent,
  Zeitfenster, Spiele-Limit; gleiche Darstellung Werktage/Wochenende bzw. jeder Tag). Solange sie nicht gespeichert
  ist, gilt in den Ferien die Wochenvorlage (Formular damit vorbelegt).
- **Gesetzliche Feiertage je Familie**: Land Deutschland, Österreich oder Schweiz plus Bundesland/Kanton (oder nur
  landesweite Feiertage); Standard „keine Feiertage“. Berechnung lokal mit der Bibliothek `holidays` (PyPI, MIT,
  gepinnt), kein Abruf zur Laufzeit. Ein Feiertag gilt **wie ein wählbarer Wochentag** (Standard Sonntag), und zwar
  aus der an dem Datum geltenden Vorlage (in den Ferien also aus der Ferienvorlage).
- Geltende Regel eines Datums: Vorlage = Ferienvorlage, wenn das Datum in einem Ferienzeitraum liegt, sonst
  Wochenvorlage; Wochentag = „gilt wie“-Tag, wenn Feiertag, sonst der echte Wochentag. Gutschriften (Tagesausnahmen)
  kommen wie bisher dazu; die Prüfung „Fensterende nach dem regulären Ende“ nutzt die geltende Regel.
- **Übertragung ohne Protokolländerung:** Die Policy enthält als `rules` die geltenden Regeln der Tage heute …
  heute+6 (jeder Wochentag genau einmal, sortiert nach Wochentag). Ohne Ferien/Feiertage im Fenster bleibt die
  Policy unverändert. Ein Hintergrundjob prüft kurz nach 00:00 Uhr Heimatzeit (Wartezeit in UTC gerechnet, damit die
  Zeitumstellung stimmt) alle verbundenen Geräte und sendet die Policy, wenn sich ihre Version geändert hat; neu verbundene Geräte bekommen sie
  ohnehin beim Handshake. Restlücke: Gerät länger als 6 Tage offline → die gecachte Woche wiederholt sich
  (`docs/limitations*.md`).
- Anzeige: Tab „Heute“ nennt Ferien bzw. Feiertag des Tages; Tab „Zeiten“ listet die Sondertage der nächsten 14 Tage.
- Export/Import der Familie enthält Ferienzeiträume, Feiertagseinstellung und Ferienvorlagen.

### Zeitkonto (entschieden 2026-11-25, Planungssitzung; umgesetzt 2026-11-25)

- **Je Kind** unter Kind → *Zeiten*, standardmäßig aus. Einstellungen: Anteil 25/50/100 % (Standard 50), höchstens
  pro Tag 5–120 min (Standard 30), Verfall nach 1–14 Tagen (Standard 7).
- **Einzahlung:** Für jeden abgeschlossenen Tag ab dem Tag nach dem Einschalten (`child.bank_since`) abgerundet
  Anteil × (Zeit pro Tag der geltenden Vorlage − Nutzung). Wochen- oder Ferienvorlage und Feiertage wie in
  `Calendar.rule_for`. Gutschriften der Eltern und genutztes Guthaben zählen nicht, sonst schaukelt sich das Konto
  auf. Der Kontostand ist auf 3 × Tageswert begrenzt.
- **Verbrauch:** An einem Tag ist `min(Tageswert, Kontostand)` zusätzlich erlaubt. Die Nutzung über Regel und
  Gutschrift hinaus verbraucht das älteste Guthaben zuerst. Eine Einzahlung von Tag d gilt an d+1 … d+Verfall.
- **Nur Gesamtzeit:** Spiele-Limit, Zeitfenster und Sperrzeiten bleiben unverändert.
- **Ausschalten** löscht das Guthaben (`bank_since = NULL`). Erneutes Einschalten beginnt wieder am Folgetag.
- **Berechnung:** im Server (`app/timebank.py`) zustandslos aus `usage_record`, `day_exception` und dem Kalender,
  höchstens 28 Tage zurück. Es gibt keine Buchungstabelle, nachträgliche Meldungen werden einfach neu gerechnet.
- **Policy:** Das heute nutzbare Guthaben ist eine zusätzliche `DayOverride` für heute. Der Agent addiert alle
  Ausnahmen eines Tages, deshalb braucht es weder ein neues Protokoll noch neue Agenten oder Tray-Änderungen.
- **Offline:** Ein Rechner setzt das Guthaben mit der gecachten Policy bis Tagesende durch, bekommt neues aber erst
  mit der nächsten Verbindung. Der Tageswechsel-Job (`vacations.run_forever`) schiebt die Policies nach Mitternacht
  nach. Meldet ein Gerät Nutzung vergangener Tage nach, gehen geänderte Policies an die online-Geräte des Kindes,
  auch an das meldende. Restlücke in `docs/limitations*` (verspätete Meldungen).
- **Anzeige:**
  - Eltern, Tab *Zeiten*: Karte „Zeitkonto“ mit Kontostand und heute zusätzlich erlaubten Minuten.
  - Eltern, Tab *Heute*: „Zeitkonto: heute +X (Konto Y)“; das erlaubte Tageskontingent, die Statistik und der
    Nutzungsbericht enthalten das Guthaben.
  - Kind: nur die höhere Restzeit in der Tray-App, ohne eigenen Hinweis (Kandidat für später).

## Programm-Whitelist

- Agent abonniert Prozess-Events über Netlink (`PROC_EVENTS`), löst `/proc/<pid>/exe` auf und
  beendet nicht erlaubte Prozesse von Kinder-UIDs.
- `/home` und `/tmp` mit `noexec` gemountet. Umsetzung (entschieden 2026-10-14):
  - **Kinder-Homes** (zugeordnete Nicht-Admins im Modus „Sperren“; entschieden 2026-11-08, vorher
    „≠ Aus“ – „Nur protokollieren“ darf nichts sperren) je per Bind-Mount
    auf sich selbst mit `noexec,nosuid,nodev` – Admin-/Eltern-Homes bleiben ausführbar
    (Grundregel). Der Agent legt die Mounts als transiente systemd-Units an (`systemd-mount`,
    Beschreibung „kidctl: noexec-Home“), beim Start vor den Logins und bei Policy-Änderungen;
    Modus nicht mehr „Sperren“ → Mount wird aufgehoben. Nur für Homes genau unter `/home/<name>`,
    kein Symlink, Eigentümer = Kind.
  - **Systemweit** `/tmp`, `/var/tmp` (Bind-Mount auf sich selbst) und `/dev/shm` mit `noexec`
    (`kidctl-noexec.service`, vom Agenten per `Wants=` mitgestartet, `ExecStop` nimmt es zurück).
    Gilt unabhängig vom Modus und für alle User (entschieden 2026-11-08): Programme, die aus diesen
    Ordnern starten, scheitern auch bei „Nur protokollieren“ und „Aus“ (bekannte Grenze).
  - Desktop-Verknüpfungen (entschieden 2026-10-18): KDE startet `.desktop`-Dateien außerhalb der
    Anwendungsordner nur, wenn sie ausführbar sind oder root gehören; bei noexec-Home ist keine
    ausführbar (Qt `isExecutable` = false, getestet). Der Agent übernimmt daher alle 10 s für Kinder
    im Modus „Sperren“ Verknüpfungen in `~/Desktop` und `XDG_DESKTOP_DIR` für root (0644), deren
    Programm das Kind starten darf (Freigabe, Basisliste, Flatpak-ID, Shell-Skript aus /usr wie
    `xdg-open`). Nur reguläre Dateien des Kindes ohne harte Links, Öffnen ohne Symlink-Verfolgung,
    `fchown` auf dem Deskriptor. Danach kann das Kind sie löschen, aber nicht ändern (Steam kann eine
    vertraute Verknüpfung nicht überschreiben – löschen und neu anlegen). Kein Zurücksetzen bei
    Entzug der Freigabe (der Start wird ohnehin vom Agenten geprüft).
  - Aufheben („Sperren“ beendet / Zuordnung entfernt) nie während der Besitzer angemeldet ist
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
    dynamischen Loader (`ld-linux*`, startet sonst beliebige Programme; erlaubt nur im reinen
    Prüfmodus von `ldd`: `--verify`/`--list` bzw. `LD_TRACE_LOADED_OBJECTS`). `socat` bleibt gesperrt
    bis auf genau den Aufruf von `pam_kwallet_init` (kwallet-pam, entsperrt KWallet beim Login):
    `socat STDIN UNIX-CONNECT:/run/user/<uid>/kwallet{5,6}.socket` mit `/usr/lib/pam_kwallet_init` als
    Vorfahr (ergänzt 2026-10-21). Dazu (ergänzt
    2026-10-20) automatisch Programme, die installierte Pakete als Hintergrunddienst der Sitzung
    eintragen: `Exec=` aus `/etc/xdg/autostart/*.desktop` und `/usr/share/dbus-1/services/*.service`,
    `ExecStart=` aus `/usr/lib/systemd/user` und `/etc/systemd/user` – nur root-eigene, nicht
    fremd-beschreibbare Dateien, nie Autostart/Units aus dem Home; gleiche Ausnahmen wie oben,
    zusätzlich `systemd-nspawn`/`-vmspawn`/`systemd-run`. Bewusst **nicht** über die Elternkette
    („von systemd --user gestartet“): Das Kind kann per `systemd-run --user`/D-Bus selbst so starten.
    Neu berechnet beim Start und nach pacman-Updates. Ist sie leer (Fehler), wird nur protokolliert – nie die Sitzung zerstört.
  - Programmliste im WebUI gruppiert nach freedesktop-Kategorien (entschieden 2026-10-16): Spiele,
    Lernen, Internet & Kommunikation, Büro, Grafik, Musik & Video, Entwicklung, Werkzeuge,
    System & Einstellungen (standardmäßig eingeklappt), Sonstiges, Nicht mehr installiert; erste
    passende Gruppe gewinnt (Spiele vor Lernen). Je Gruppe Zähler „x von y erlaubt“ und „Alle/Keine“,
    darüber Suchfeld (kleines JavaScript; ohne JS bleibt die Liste bedienbar). Pfad nur als Tooltip.
  - Shells (aus `/etc/shells`: bash, fish, zsh …, je Programmdatei einmal) meldet der Agent zusätzlich
    als Einträge „<name> (Shell)“, Kategorie `Shell` – sie haben keinen Startmenü-Eintrag. Gruppe
    „Terminal & Shells“ (mit Terminal-Programmen wie Konsole). Freigegebene Shell = auch interaktiv
    erlaubt. Shells zählen nicht zur Programmzeit (Systemprozesse `sh -c …`).
  - Eltern-Liste: App-Schlüssel `exe:<pfad>` bzw. `flatpak:<app-id>` aus dem gemeldeten Katalog
    (systemweite .desktop-Einträge; nicht `~/.local/share/applications`). Hilfsprogramme im
    Verzeichnis einer freigegebenen App (z. B. `/usr/lib/firefox/*`) sind mit erlaubt.
  - Shells nur ohne Steuerterminal (Skripte von Systemkomponenten); Interpreter nur mit
    freigegebener Skriptdatei (kein `-c`/`-m`/`-e`, stdin, REPL). Zusätzlich gesperrt (entschieden 2026-10-18), wenn Code
    eingeschleust werden kann: Code-Variablen gesetzt (`PYTHONSTARTUP`, `PYTHONINSPECT`, `PERL5OPT`,
    `RUBYOPT`, `NODE_OPTIONS`, `LUA_INIT*`, `R_PROFILE*`, `PHPRC`, …), Modul-Suchpfade (`PYTHONPATH`,
    `PYTHONUSERBASE`, `PERL5LIB`, `RUBYLIB`, `NODE_PATH`, `LUA_PATH`, …) mit Einträgen außerhalb von
    `/usr`, `/etc`, `/opt` oder leer/relativ (= aktuelles Verzeichnis), oder bei Python ein nicht
    leerer User-Site-Ordner `~/.local/lib/python*/site-packages` (`.pth`, `usercustomize.py` und dortige
    Module laufen automatisch vor den System-Paketen). Von Steam/Launchern gestartete Prozesse sind
    ausgenommen. kidctl-Wrapper starten daher
    Skriptdateien unter `/usr/lib/kidctl/`.
- Server: Katalog je Gerät, Freigaben je Kind (`child_program`), Sperrprotokoll (`blocked_event`),
  WebUI-Abschnitt „Programme“ und „Gesperrte Aktionen“ auf der Kind-Seite.
- **Protokollseite** `/children/<id>/protokoll` (2026-10-19): Filter nach Art (Alle/Programme/
  Websites), Zeitraum (Heute/7 Tage/30 Tage/Alles; Tagesgrenzen in `TZ_HOME`), Status (gesperrt/nur
  protokolliert), Benutzer (ab zwei), Freitextsuche (Name, Schlüssel, Detail; ohne Platzhalter),
  Reihenfolge; Einträge nach Tagen gruppiert, je 200 nachladbar; Kasten „Häufigste Einträge“ im
  gefilterten Zeitraum (Klick filtert darauf). Alles per GET-Parametern (Links teilbar, ohne
  JavaScript). Die Kind-Seite zeigt die letzten 10 Einträge mit Links zur Protokollseite. Einträge werden nach
  90 Tagen gelöscht (Server-Aufgabe beim Start und alle 6 Stunden; entschieden 2026-10-19).
- **Eigene Programme** (entschieden 2026-10-18): Eltern tragen im Dialog „Programm hinzufügen“ ein
  Programm ein, das nicht im Katalog steht (Pfad oder Name aus `/usr/bin`, z. B. `git`; optional
  Anzeigename). Es wird sofort freigegeben und erscheint in der Gruppe „Eigene Programme“ (an-/abwählbar,
  entfernbar – Entfernen entzieht die Freigabe). Tabelle `child_custom_program`. Pfad nur absolut,
  normalisiert, Zeichen `A-Za-z0-9._+@/-`. Der Agent berechnet die Paket-Ordner auch für diese Programme
  (z. B. `/usr/lib/git-core` für git) und rechnet nach einer neuen Freigabe neu, ohne sie aus der
  Basisliste zu nehmen.
- Browser: nur Firefox/Chromium mit gesetzter Policy sollten freigegeben werden.

### Programmquellen
- **Pacman/AUR:** Erkennung über Binary-Pfad.
- **Flatpak (nur systemweit):** Erkennung über App-ID (Cgroup `app-flatpak-<appid>-*.scope`
  bzw. `/proc/<pid>/root/.flatpak-info`). `flatpak --user` wird nicht unterstützt.
- **Steam:** nur als Ganzes freigegeben/gesperrt.
  - Bibliothek unter `/srv/steam/<user>` (ohne `noexec`, Eigentümer Kind, 0700).
  - Verweis + Bibliothek für **jedes zugeordnete Kind**, unabhängig vom Programm-Modus (sonst legt
    Steam bei ausgeschalteter Kontrolle einen echten Ordner im Home an, der nach dem Einschalten
    nicht mehr startet – Nutzer-Test 2026-10-16).
  - Vorhandener Steam-Ordner im Home wird nach der Abmeldung automatisch nach `/srv/steam/<user>`
    verschoben (entschieden 2026-10-16): nur per `renameat` (gleiche Partition), jeder Pfadteil ohne
    Symlink-Verfolgung geöffnet und auf das Kind als Besitzer geprüft, Ziel muss leer sein. Sonst
    (andere Partition, Ziel nicht leer, Verweis woandershin) Integritätsfehler `steam:<user>` mit
    Hinweis; Anleitung zum Umzug von Hand in `docs/installation.md`.
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
- **Paket-Ordner:** Verzeichnisse, die ausschließlich dem Pacman-Paket einer freigegebenen App
  gehören (oberstes je Zweig unter `/usr/lib`, `/usr/libexec`, `/usr/share`, `/opt`), gelten wie das
  Verzeichnis der App (z. B. `/usr/lib/heroic-games-launcher` für `/usr/bin/heroic` – mit `app.asar`
  und den Hilfsprogrammen `gogdl`, `legendary`, `nile`). Berechnet aus `pacman -Ql` zusammen mit der
  Basisliste (entschieden 2026-10-18).
- **Electron (System-Electron `/usr/lib/electronNN/`):** keine eigene Freigabe. Hauptprozess erlaubt,
  wenn seine App (erstes Nicht-Options-Argument, z. B. `…/app.asar`) freigegeben ist, ohne
  `-r/--require`, `-e`, `-p`, `-i`, `--inspect*`, `--remote-debugging*`, `--js-flags` und ohne
  `ELECTRON_RUN_AS_NODE`/`NODE_OPTIONS` in der Umgebung. Hilfsprozesse (`--type=…`, Crashpad) nur als
  Nachfahren eines erlaubten Hauptprozesses. Andere Electron-Apps bleiben gesperrt.
- **Spiele-Launcher (Heroic, Lutris):** Ist der Launcher (`/usr/bin/heroic`, `/usr/bin/lutris`)
  freigegeben, sind seine Nachfahren erlaubt (Wine, Proton, Spiele) – wie bei Steam. Proton aus der
  Steam-Bibliothek nur, wenn auch Steam freigegeben ist. Gleiche Restlücke („Spiel hinzufügen“).
  - Heroic-Ordner unter `/srv/heroic/<user>` (Eigentümer Kind, 0700): `~/Games/Heroic` → `Games`
    (Spiele, Wine-Prefixe), `~/.config/heroic/tools` → `tools` (Wine/Proton), `~/.local/share/umu` →
    `umu` (Laufzeit, mit der Heroic Proton startet), `~/.cache/heroic_gogdl` und `~/.cache/legendary`
    → `cache/…` (gogdl/legendary entpacken dort native Module und laden sie). Anlage, Umzug nach der
    Abmeldung und Integritätsmeldung (`heroic:<user>`) wie bei Steam; nur wenn Heroic installiert ist.
    Andere Installationsordner (in Heroic einstellbar) liegen im Home und starten bei Kontrolle nicht.
  - `gogdl`/`legendary` sind Python-Zipapps (Interpreter-Regel, Skript im Paket-Ordner).
    `nile` (Amazon) ist eine PyInstaller-Einzeldatei, die sich nach `/tmp` entpackt – scheitert an
    `noexec`-`/tmp` (bekannte Grenze, nicht gelöst).
  - Lutris: Regel vorbereitet, ohne eigene Ordner und ungetestet.
- **Spieleordner ohne Freigabe `noexec`** (entschieden 2026-10-18; seit 2026-11-08 nur im Modus „Sperren“): Für Kinder im Modus „Sperren“
  hängt der Agent `/srv/steam/<user>` bzw. `/srv/heroic/<user>` per `systemd-mount` `noexec,nosuid,nodev`
  ein (Beschreibung „kidctl: noexec-Spieleordner“), solange Steam bzw. Heroic nicht freigegeben ist.
  Grund: Diese Ordner gehören dem Kind und sind sonst die einzigen ausführbaren, beschreibbaren Orte –
  eigene Bibliotheken ließen sich per `LD_PRELOAD`, Vulkan-Layer (`~/.local/share/vulkan/implicit_layer.d`)
  o. Ä. in erlaubte Programme laden, ohne dass ein Programmstart sichtbar wird. Eine reine
  Umgebungsprüfung (`LD_*`) wurde verworfen (Vulkan-Layer brauchen keine Variable). Aufheben sofort bei
  Freigabe (keine Sitzung hängt daran; bei „busy“ nächster Abgleich). Integrität `noexec:<pfad>`.
  Bei Freigabe bleibt die bekannte Lücke „Spiel hinzufügen“.
- **Spielzeit je Spiel und „spielt gerade“** (entschieden 2026-10-19, Varianten 1 + 2): Der Agent erkennt
  Spielprozesse an der Umgebung (`SteamAppId`; Heroic `HEROIC_APP_NAME` + `HEROIC_APP_SOURCE`) – gelesen nur
  bei Kandidaten (Spieleordner, Wine/pressure-vessel, Container), je Prozess zwischengespeichert. Spielname
  aus `steamapps/appmanifest_<id>.acf` bzw. Heroics Bibliotheksdaten im Home (ohne Symlink-Verfolgung,
  nur reguläre Dateien, Größenlimit). Programmzeit unter `game:steam:<id>` / `game:heroic:<quelle>:<id>`
  (Spielprozesse zählen für das Spiel, nicht mehr für Steam); Name in `AppUsageEntry.name`. Laufende
  Spiele in `AppUsageReport.playing` (abwärtskompatibel, Report wird jetzt jede Minute gesendet, auch
  leer). Server: Spalte `app_usage.name`, „spielt gerade“ nur im Speicher (verfällt nach 150 s oder bei
  getrenntem Gerät), Anzeige in Übersicht, Kind- und Statistikseite. Namen/IDs stammen aus Dateien des
  Kindes: nur Anzeige, keine Freigaben.
  - **Lutris und Bottles** (Roadmap `wine-launchers`, 2026-11-23; Formate aus echten Installationen in der Test-VM,
    Testdaten `agent/tests/fixtures/wine_launchers`): Lutris setzt im Spielprozess `GAME_NAME` und
    `LUTRIS_GAME_UUID` (je Start neu) → `game:lutris:<Name>`, nur wenn beide gesetzt (UUID-Format). Bottles setzt nur
    `BOTTLE` und `WINEPREFIX`; das Programm ergibt sich aus argv[0] des Wine-Prozesses (`C:\…` → `drive_c/…`,
    Groß-/Kleinschreibung egal, sonst gleicher Dateiname) und `External_Programs` in `<WINEPREFIX>/bottle.yml` →
    `game:bottles:<Bottle>:<Programm-ID>`, Name aus der Liste. Wine-Hilfsprozesse und nicht eingetragene Programme
    zählen nicht. `bottle.yml` ohne YAML-Paket gelesen (nur `name`/`path` je Programm, PyYAML-Ausgabe mit
    `\xNN`-Escapes und umbrochenen Werten), nur reguläre Datei des Kindes (Besitzer = UID des Prozesses, kein
    Symlink, ≤ 1 MiB). Kandidaten wie bisher (Wine-Binärname, Container). Bottles- und Lutris-Flatpak
    (Desktop-Kategorie „Game“) zählen als Launcher, nicht als Spiel. Nicht erkannt: Linux-Spiele aus Lutris (kein
    Kandidat), Bottles-Programme ohne Listeneintrag – Restlücke in `docs/limitations*.md`. Programmkontrolle
    unverändert (keine eigenen Spieleordner; im Modus „Sperren“ starten die Spiele aus dem noexec-Home i. d. R. nicht).
  - **Spiel-Protokoll** (Reiter „Spiele“ der Protokollseite, 2026-10-19): Der Server schreibt aus den
    „spielt gerade“-Meldungen Sitzungen fort (`game_session`: Start, letzte Meldung; Lücke > 3 min =
    neue Sitzung; Dauer minutengenau, eine Meldung = 1 min). Anzeige nach Tagen mit Tagessumme,
    „seit HH:MM ▶ läuft“ für laufende Sitzungen, „Meistgespielt“ (Summe, Anzahl Sitzungen) im
    Zeitraum, Suche/Zeitraum/Benutzer/Reihenfolge wie bei den Sperren. Löschung nach 90 Tagen wie das
    übrige Protokoll. Kein Agent-Update nötig.
- **Container-Prozesse** (entschieden 2026-10-19): Bei Prozessen in einem eigenen Mount-Namespace
  (nicht Flatpak) zeigt ein Pfad nicht zwingend auf die Systemdatei. Der Agent vergleicht deshalb die
  tatsächlich laufende Datei (`/proc/<pid>/exe`) sowie Skript bzw. Electron-App (`/proc/<pid>/root/…`)
  per Gerät + Inode mit der Systemdatei unter demselben Pfad. Weicht etwas ab, ist der Prozess nur als
  Laufzeit-Container von Steam (freigegeben) oder einem freigegebenen Launcher erlaubt; abweichende
  Prozesse gelten auch nie als Launcher- oder Electron-Hauptprozess für ihre Nachfahren. Referenz ist
  der Mount-Namespace von PID 1. Restlücke: Steam-Vorfahren werden nicht so geprüft (Steam-Client-Pfade
  liegen in der Bibliothek des Kindes und sind bei Freigabe ohnehin ersetzbar).
- **Abstammung gemerkt:** Für Prozesse, die nur über ihre Elternkette erlaubt sind (Steam,
  Launcher, Electron-Hilfsprozesse), merkt sich der Agent die Kette beim Start. Verwaiste Prozesse
  (z. B. `wineserver`) werden nach einer Policy-Änderung so nicht beendet; wird die Freigabe
  entzogen, schon. Zusätzlich (2026-10-20) merkt sich der Agent aus Netlink-`fork`-Events den echten
  Elternprozess jeder PID (begrenzt auf 65 536 Einträge): Endet der Elternprozess und wird das Kind an
  einen Subreaper (`systemd --user`) umgehängt, oder ist er bei der Prüfung schon beendet, folgt die
  Kette den fork-Eltern weiter (PID-Wiederverwendung: Vorfahr muss vor dem Kind gestartet sein).

### Restlücken – Hintergrund
(Elternfassung in `docs/limitations.de.md`; entschieden bei den jeweiligen Umsetzungen, aus „Bewusst nicht umgesetzt“
hierher verschoben 2026-11-17.)
- Kein `flatpak --user`, keine Pro-Spiel-Steuerung bei Steam und Heroic.
- „Nicht-Steam-Spiel hinzufügen“ als bekannte Restlücke. Gleiche Wirkung: Die Steam-Bibliothek gehört dem Kind
  (Steam aktualisiert sich selbst) – `steam.sh`/Client sind ersetzbar. Wer Steam freigibt, gibt faktisch alles frei,
  was über Steam gestartet werden kann.
- Snap/Flatpak: Freigabe gibt alles in der Sandbox frei (auch `snap run --shell`/`flatpak run --command`). Unter
  Ubuntu ist „Dateien“ immer erlaubt (Desktop-Symbole).
- Plasma-Widgets und GNOME-Erweiterungen des Kindes laufen als QML/JavaScript in der Shell und sind keine
  Programmstarts.
- Shell-Befehle (`sh -c …`) über KRunner o. Ä. sind erlaubt (Systemkomponenten brauchen das); ausführbar sind damit
  nur erlaubte Programme – eigene Programme scheitern an `noexec`, Interpreter-Code (`python -c`, Skripte aus dem
  Home) an der Interpreter-Regel.
- Shell-Skripte aus `/usr`, `/etc`, `/opt` sind immer erlaubt (das gestartete Programm wird geprüft); reine
  Shell-Skript-Anwendungen unter `/usr` sind daher nicht sperrbar.
- Programme, die freigegebene Apps intern aufrufen (z. B. `git` aus Kate), sind gesperrt, solange sie nicht selbst
  freigegeben werden (`git` bewusst nicht in der Basisliste: Aliase/Hooks).

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
    Doppelte: DoH-Anbieter > SafeSearch > Freigaben > Sperren/Listen; SafeSearch entfällt für Namen, die
    (auch über die Hauptdomain) gesperrt und nicht freigegeben sind (2026-11-22).
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

### Restlücken – Hintergrund
(Elternfassung in `docs/limitations.de.md`, hierher verschoben 2026-11-17.)
- Keine IP-Bindung im ersten Release: direkte IP-Adressen (auch DoH über IP) umgehen den DNS-Filter. VPN-Verkehr mit
  fest eingetragenen IP-Adressen liegt außerhalb des Threat Models.
- Programme, die systemd-resolved über D-Bus/Varlink fragen (z. B. `resolvectl`), umgehen die Umleitung; Browser und
  glibc-Programme nicht (nsswitch angepasst).
- Zwischen zwei Integritätsprüfungen (≤ 60 s) kann eine entfernte nftables-Tabelle den DNS-Filter offen lassen.
  `nftables.service` leert beim (Neu-)Laden den gesamten Regelsatz (`flush ruleset`) – der Agent stellt seine Tabelle
  im nächsten Takt wieder her.
- Eine Freigabe-Ausnahme im Blacklist-Modus gilt nicht für spezifischere Einträge einer Sperrliste.

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
  `chat_sync` nach (ab der ältesten noch offenen Anfrage, sonst ab der höchsten bekannten
  Nachricht – so kommen auch offline gefallene Entscheidungen an). Desktop-Benachrichtigung bei neuer Eltern-Nachricht und bei Entscheidung über
  eine Anfrage. Nachrichten, die vor der Verlaufsanfrage entstanden sind, gelten als Verlauf: beim ersten
  Abgleich eines Kindes auf dem Gerät (neue Zuordnung, Zuordnung gewechselt, Neuinstallation, leerer Cache)
  landen sie still im Chat; bei späteren Abgleichen (z. B. nach Offline-Phase) gibt es für nachgeholte
  Eltern-Nachrichten genau eine Meldung – bei einer die Nachricht selbst, sonst „N neue Nachrichten“.
  Linux und Windows gleich (gemeinsamer Agent-Kern).
- Vorwarnungen und Zeitende-Meldung haben den Knopf „Verlängerung anfordern“
  (`notify-send --action … --wait`); Klick öffnet den Anfrage-Dialog der Tray-App.
- Zustellung: Agent leitet Nachrichten der Tray-App per WebSocket weiter (`chat_send`,
  `extension_request`) und puffert sie offline; `client_msg_id` macht das Senden idempotent,
  der Server bestätigt mit `ack`. Server stellt neue/geänderte Einträge als `chat_message` an
  alle online-Geräte des Kindes zu (Upsert per `id`); Verlauf per `chat_sync` nachholbar.
- Eltern-WebUI aktualisiert den Chat per Polling (5 s).
- Verlängerungsanfragen für vergangene Tage verfallen automatisch (Status `expired`).
- Gleichzeitige Entscheidungen zweier Eltern: nur die erste wird übernommen.
- **Freigabe-Anfragen** (entschieden 2026-10-20): Das Kind bittet um Freigabe eines gesperrten
  Programms oder einer Website; Eltern antworten mit **Dauerhaft erlauben**, **Nur heute** oder
  **Ablehnen** (Chat-Eintrag `access_request`, Push an alle Eltern).
  - Programme: Sperr-Benachrichtigung mit Knopf „Freigabe anfragen“ (nur, wenn eine Freigabe die Sperre
    auch aufhebt – nicht bei Container-Abweichung, eingeschleustem Code, Steam-Bibliothek ohne Steam).
  - Websites: Tray-Menü „Gesperrt – Freigabe anfragen …“ listet die in den letzten 24 h gesperrten
    Programme und Websites (Websites je Hauptdomain zusammengefasst, Näherung ohne Public-Suffix-Liste;
    ohne DoH-Anbieter und Werbe-/Tracker-Listen, die nicht protokolliert werden).
  - Grund optional (Dialog in der Tray-App). Anfragbar ist nur, was der Agent selbst gesperrt hat;
    Name und Schlüssel stammen aus seiner Liste, nicht aus der Eingabe. Je Kind und Schlüssel höchstens
    eine offene Anfrage.
  - Dauerhaft: Katalog-App -> Programmfreigabe; anderes Programm -> „Eigene Programme“; Website ->
    erlaubte Domain inkl. Subdomains (eigene Sperre derselben Domain wird entfernt).
  - Nur heute: `child_temp_grant` (lokaler Tag), in der Policy als `temp_grants`. Der Agent wendet sie
    nur am jeweiligen Tag an und läuft um Mitternacht auch offline ab: Programme werden neu geprüft (noch
    laufende danach beendet), Spielordner und Website-Filter angeglichen.
  - Server zuerst aktualisieren: ältere Server lehnen die neue Nachricht `access_request` ab.

## Eltern-Accounts & Sicherheit

- Mehrere gleichberechtigte Eltern-Accounts; jeder sieht und verwaltet alle Kinder.
- Bootstrap des ersten Accounts per CLI-Skript, weitere im WebUI.
- TOTP-2FA verpflichtend (`pyotp`).
- Login-Rate-Limiting je 15 min: 5 Fehlversuche pro IP, 50 pro Account, 5 falsche Codes bei richtigem Passwort pro Account (ein Fremder ohne Passwort darf niemanden aussperren).
- Sessions 30 Tage gültig.
- CSRF-Schutz auf allen Formularen/Aktionen.
- Push-Abos pro Account und Endgerät.

## Anmeldung, Oberfläche und Prüfungen (entschieden 2026-11-20 per /brainstorming)

Ziel: Eltern melden sich bequemer an, ohne dass ein Elternkonto schwächer geschützt wird; stimmiges Erscheinungsbild über Elternoberfläche, Verwaltung, Login und Tray-App.
Anti-Ziele: Elternkonto mit einem einzelnen schwachen Faktor erreichbar; klassischer Login fällt weg oder braucht JavaScript; die Anmeldung verrät, ob eine E-Mail-Adresse ein Konto hat; Linux-Kinder sehen keine Restzeit mehr.

### Login in zwei Schritten (Eltern und Betreiber)

- **Schritt 1:** E-Mail (bzw. Benutzername) + Passwort → „Weiter“. **Schritt 2:** Authenticator-Code.
- Zwischen den Schritten ein **Login-Ticket**: serverseitig gespeichert (Tabelle, nur Hash des Tokens), Token im
  Cookie (`HttpOnly`, `Secure`, `SameSite=Strict`), **5 Minuten** gültig, **einmal** verwendbar, gebunden an Konto
  und Art (Eltern/Betreiber). Abgelaufen/ungültig → zurück zu Schritt 1 mit neutralem Hinweis.
- **Keine Kontoauskunft:** falsches Passwort und unbekannte E-Mail → identische Antwort und vergleichbare Laufzeit
  (`dummy_verify` bleibt). Schritt 2 erscheint nur nach korrektem Passwort.
- **Sperren:** bestehende Ratenbegrenzung je IP/Konto für Schritt 1; TOTP-Fehlversuche weiter je Konto
  (`totp_locked`), zusätzlich max. 5 Code-Versuche je Ticket, danach Ticket ungültig.
- Funktioniert **ohne JavaScript** (zwei normale Formulare). Bestehende Flows (Authenticator einrichten,
  Passwort vergessen, Authenticator-Reset-Link) bleiben erhalten und werden an den neuen Ablauf angepasst.
- Betreiber-Login (`/admin/login`) identisch, eigene Tickets.

### Passkeys als vollständiger Login (Variante A)

- **Bibliothek:** `webauthn` (py_webauthn, Duo Labs, BSD-3) in `server/requirements.txt` gepinnt.
- **Loginseite Schritt 1:** E-Mail-Feld mit `autocomplete="username webauthn"` (Browser schlägt gespeicherte Passkeys
  vor, „conditional UI“) **und** Button „Mit Passkey anmelden“. Erfolgreicher Passkey → angemeldet, **ohne** Passwort
  und Code. Ohne Passkey normaler Ablauf (2.1). Ohne JavaScript bzw. ohne WebAuthn-Unterstützung: Button
  ausgeblendet, klassischer Login unverändert.
- **Parameter:** discoverable credentials (`residentKey=required`), `userVerification=required` (Biometrie/Geräte-PIN
  = zweiter Faktor), `attestation=none`, Algorithmen ES256 und RS256. Challenge serverseitig, einmalig, 5 Minuten.
- **RP-ID:** `WEBAUTHN_RP_ID` (Standard: Host aus `BASE_URL`), Anzeigename = Branding-Name. Origin-Prüfung gegen
  `BASE_URL`. Hinweis in der Doku: Passkeys sind an die Domain gebunden; bei Domain-Wechsel neu anlegen (oder
  später Related Origin Requests über `/.well-known/webauthn`).
- **Verwaltung im Profil** (Eltern und Betreiber): Liste der Passkeys (Name, angelegt, zuletzt benutzt,
  „synchronisiert“ ja/nein), hinzufügen, umbenennen, löschen. **Hinzufügen und Löschen nur nach frischer
  Bestätigung** (Passwort + Code oder ein vorhandener Passkey, nicht älter als 5 Minuten). Mehrere Passkeys je Konto.
- **Speicherung:** je Passkey Credential-ID, öffentlicher Schlüssel, Sign-Count, Transports, Backup-Status, Name,
  Zeiten; Zuordnung zum Konto. Ablage wie die übrigen Konto-Anmeldedaten (Plattform-Bereich, damit die Anmeldung
  vor der Familienzuordnung funktioniert); Namen der Passkeys verschlüsselt wie andere Kontodaten.
- **Sign-Count:** Rückgang bei nicht synchronisierten Schlüsseln → Anmeldung ablehnen und protokollieren
  (synchronisierte Passkeys liefern oft 0 – dann keine Prüfung).
- **Sicherheitsereignisse:** Hinzufügen/Löschen eines Passkeys → Hinweis-Mail an das Konto („Neuer Passkey …“) und
  bei Betreibern Eintrag im Verwaltungsprotokoll.
- **Wiederherstellung und Entzug:** „Passwort vergessen“ (neues Passwort gesetzt) und Authenticator-Reset (auch durch
  den Inhaber für ein Mitelternteil) **löschen alle Passkeys des Kontos**; die Hinweis-Mail sagt das („Deine Passkeys
  wurden entfernt – bitte neu anlegen“). „Alle Sitzungen beenden“ lässt Passkeys bestehen. Andere Eltern sehen die
  Passkeys eines Mitelternteils nicht. Gleiches gilt für Betreiber (Reset per CLI löscht Passkeys).
- **Frische Bestätigung – gemeinsamer Mechanismus:** gilt für **Passwort ändern, Authenticator ändern und Passkeys
  hinzufügen/löschen** (Eltern und Betreiber): Passwort + Authenticator-Code **oder** ein vorhandener Passkey, nicht
  älter als 5 Minuten (Zeitstempel an der Sitzung). Die vier bestehenden Abläufe (`profile/password`,
  `profile/totp/confirm`, je Eltern und Betreiber), die heute nur das Passwort verlangen, werden darauf umgestellt.
- **Rückfall:** Passwort + Authenticator bleibt immer möglich; Passkeys ersetzen den Authenticator **nicht** bei der
  Kontoeinrichtung (TOTP bleibt Pflicht bei Registrierung).
- Sitzungen nach Passkey-Login wie bisher (Laufzeit, Cookie).

### Homepage-Link auf der Loginseite

- Neues Feld **„Homepage“** (URL + Linktext, DE/EN) auf der Seite *Erscheinungsbild* (Branding), nur *Admin*,
  protokolliert. Nur `https://`-URLs.
- Link unter dem Login-Formular, auch auf Registrierung, Passwort vergessen, Authenticator-Einrichtung und dem
  Betreiber-Login.
- Leer → Link zur Projektseite (Standard-URL der kidCTL-Homepage); im Einzelbetrieb ebenso.

### Einladungsmail für Einladungscodes

- Formular „Einladungscode erzeugen“ bekommt ein optionales Feld **E-Mail-Adresse** (+ Sprache DE/EN).
- Ausgefüllt → beim Erzeugen Mail im HTML-Layout (`mail/base.html`) mit Code, Registrierungslink mit eingesetztem
  Code (`/signup?code=…`), Ablaufdatum und Tarif bzw. Testphase. Neuer Mailtyp in `/admin/mail-preview` + Tests.
- Adresse wird gespeichert (Plattform-Schlüssel), damit **„Erneut senden“** möglich ist; **gelöscht**, sobald der
  Code eingelöst oder abgelaufen ist (Aufräumjob).
- Registrierung mit Code-Link: E-Mail-Feld mit der Einladungsadresse vorbelegt, änderbar. **Die E-Mail-Bestätigung
  bleibt unverändert** (der Code kann auch ohne Mail weitergegeben werden; der Link beweist kein Postfach). *(Entscheidung
  Claude, Builder-Check)*
- Liste der Codes zeigt „per Mail an … (maskiert)“ und den Button „Erneut senden“ (nur offene Codes).
- Code-Klartext erscheint wie bisher einmal auf dem Bildschirm; beim erneuten Senden wird **ein neuer Code**
  erzeugt (nur der Hash ist gespeichert) und der alte ungültig.

### Verwaltungsbereich mit Eltern-Navigation

- Gleiche Navigationskomponente wie `base.html` (`.navbar`, `nav-item`, Icons aus `_icons.html`) statt der Reiter in
  `_admin_head.html`.
- **Desktop:** Leiste links mit Icon + Text für alle Bereiche (je nach Rolle): Familien, Tickets, Rechnungen,
  Einladungscodes, Newsletter (SaaS), Mails, Erscheinungsbild, Protokoll, Profil.
- **Mobil:** untere Leiste mit vier Einträgen – Familien, Tickets, Rechnungen, **Mehr** (Unterseite mit den übrigen
  Bereichen). Für *Support*: Familien, Tickets, Protokoll, Mehr.
- „Abmelden“ ans Ende der Leiste bzw. ins Profil; Kopfzeile ohne Reiter, nur Seitentitel (und Kontextaktionen).
- Fehlende Icons im selben Stil ergänzen.

### Tray-Symbol unter Linux wie unter Windows

- Linux nutzt dasselbe monochrome Symbol mit Statuspunkt wie Windows (`winstyle.tray_icon`), passend zur hellen bzw.
  dunklen Leiste (Plasma: Farbschema; GNOME: dunkle Leiste als Standard).
- Restzeit unter Linux nur noch in **Tooltip und Menü**; Warnungen (5/1 min) und Countdown-Fenster unverändert.
- In der VM prüfen: Plasma und GNOME (AppIndicator) zeigen Symbol, Statuspunkt, Tooltip und Menü-Restzeit. Zeigt
  GNOME den Tooltip nicht: in `docs/limitations*.md` vermerken (Restzeit dann über das Menü).
- Restliches Linux-Design bleibt nativ (nur das Symbol ändert sich).

### Screenshot „Tray-App mit Restzeit“ für die Homepage

- In der Windows-11-VM über `dev/winshots.py`: Tray-Menü geöffnet mit Restzeit, zusätzlich Tooltip über dem Symbol;
  hell und dunkel, DE und EN. Einbinden auf der Startseite (Funktionen/Tray) und im Wiki (`child-windows`).

### Design-Durchsicht aller Seiten

- Playwright-Skript fotografiert **alle** Seiten: Elternoberfläche (alle Menüpunkte und Kinder-Unterseiten),
  Verwaltung (alle Bereiche, Detailseiten), Login/Registrierung/Passwort/Authenticator, Fehlerseiten – jeweils mobil
  (390 px) und Desktop (1280 px), hell und dunkel, mit Demo-Daten.
- Prüfliste: Buttons einer Gruppe auf gleicher Höhe und **vertikal/horizontal mittig** (Text im Button zentriert,
  Button-Gruppen bündig), einheitliche Abstände (Raster des Themes), Formularfelder bündig und gleich breit,
  Beschriftungen über Feldern, keine abgeschnittenen oder umbrechenden Button-Texte, Karten mit gleichem Innenabstand,
  Tabellen/Listen mobil lesbar, Fokus sichtbar.
- Korrekturen **zentral im CSS** (Komponentenklassen), Einzelfixes nur wo nötig; keine Inline-Styles.
- Ergebnis: Bilder **vorher/nachher** in `dev/design-review/` (**nicht committet**, in `.gitignore`); committet wird nur
  die Fundliste `dev/design-review/FINDINGS.md`. Reihenfolge: erst alle Seiten fotografieren und Funde sammeln, dann
  korrigieren, dann erneut fotografieren; Adrian nimmt **danach** anhand vorher/nachher ab. Danach einmal
  `dev/screenshots.py` für Website/Wiki neu laufen lassen. *(Entscheidung Claude, Builder-Check)*
- Skript: `dev/design-review.py --label vorher|nachher` (eigener Server im SaaS-Modus auf Port 8001, eigene Datenbank
  `kidctl_review`, Demo-Daten aus `dev/demo_seed.py`, Betreiber per CLI); meldet zusätzlich Konsolenfehler
  (CSP-Verstöße) und waagerechtes Scrollen.
- **CSP ohne `'unsafe-inline'`** (Issue #3): Templates außer Mails haben keine `style`-Attribute und keine
  `<style>`-Blöcke (Test). Diagrammmaße als Klassen `p-0`…`p-100` (`web.pct`). Mail-HTML in der Mail-Vorschau
  (Verwaltung) und der Berichtsvorschau (Profil) wird per `mailer.csp_safe` umgeschrieben (style-Attribute → Regeln
  in `<style nonce>`); nur diese Antworten erlauben ihre Nonce. JS setzt keine Styles (nur `hidden`).

### Transparenz & Sicherheit: automatische Prüfungen mit Badges

- **Grundsatz:** kein Siegel „Sicherheit geprüft durch …“. Externe Gratis-Scanner prüfen Konfiguration
  (TLS, Header, DNS, Repo-Praktiken), nicht die Anwendung. Beschriftung auf Homepage/README: **„Automatisch geprüft“**
  mit Datum und Links; Hinweis auf `SECURITY.md` für echte Meldungen.
- **OpenSSF Scorecard:** Workflow `.github/workflows/scorecard.yml` (`ossf/scorecard-action`, Actions per SHA
  gepinnt, wöchentlich + bei Push auf `main`, `publish_results: true`, `id-token: write`, SARIF in Code Scanning);
  Badge in README (DE/EN) und Homepage.
- **OpenSSF Best Practices (Passing):** Claude Code bereitet die Antworten auf alle Passing-Kriterien in
  `dev/openssf-best-practices.md` vor (mit Belegen/Links ins Repo) und listet fehlende Punkte; Adrian legt das
  Projekt auf bestpractices.dev an und trägt ein; Badge danach in README/Homepage.
- **Monatlicher Scan** `.github/workflows/websecurity.yml`: OWASP ZAP **Baseline** (passiv, keine Angriffe) und
  MDN HTTP Observatory gegen `app.kidctl.de` (Login-Seite und öffentliche Seiten, ohne Anmeldung); Funde ab
  Stufe „Medium“ bzw. Note schlechter als A → GitHub-Issue (ohne Details zu Lücken öffentlich, nur „Scan-Befund,
  siehe Workflow-Lauf“). Observatory-Note mit Link in der Homepage-Box.
- **Später (nicht heute):** internet.nl (100 % braucht IPv6 + DNSSEC, Badge nur für exakt die getestete Domain) mit
  der Produkt-Domain; SSL-Labs-Note mit Link.

### Google-Login (Recherche, Bau später)

**Entscheidung (abgestimmt, Variante D):** Google ersetzt das Passwort. Meldet Google im ID-Token `amr` mit `mfa`,
ist die Anmeldung vollständig; fehlt `mfa` oder der Claim, folgt der kidCTL-Zweitfaktor (Authenticator-Code oder
Passkey). Klassischer Login bleibt.

**Fakten (Stand November 2026):**

- Protokoll OpenID Connect; Python-Bibliothek Authlib. Scopes nur `openid email profile` – laut Google
  „nicht sensibel“, keine Pflichtprüfung; Brand-Verifizierung nur nötig, wenn Name/Logo im Zustimmungsfenster
  erscheinen sollen (typisch 2–3 Werktage; alle Domains von Homepage, Datenschutzerklärung und Redirect-URIs müssen
  verifiziert sein).
- **„Security bundle“:** Claims `auth_time` und `amr` (Werte u. a. `mfa`, `pwd`, `swk` = Passkey, `hwk` =
  Sicherheitsschlüssel, `sms`, `tel`). Voraussetzung: App „In production“ **und** „Verified“, in den erweiterten
  Einstellungen „Authentication strength claims“ aktiviert. `amr` kann trotzdem fehlen.
- **MFA erzwingen ist nicht möglich** – nur auswerten. 2SV legt der Kontoinhaber fest (oder Workspace-Admins).
- Eindeutige ID ist `sub`, **nicht** die E-Mail. Google empfiehlt zusätzlich „Cross-Account Protection“ (RISC).

**Vorschläge für den späteren Bau:**

- Google-Konto nur **im eingeloggten Profil verknüpfen** (nie automatisch per gleicher E-Mail); Login-Button erst bei
  verknüpftem Konto wirksam. Registrierung per Google: später entscheiden.
- Konfiguration über `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`), ohne Werte unsichtbar – für Self-Hosting
  optional, jede Instanz eigene Zugangsdaten.
- Datenschutzerklärung: Google als Empfänger von Anmeldedaten, Drittlandübermittlung.
- Zeitpunkt: nach Domainkauf und Datenschutzerklärung (Verifizierung); vorher funktioniert D, aber immer mit
  Zweitfaktor.

**Quellen:** developers.google.com (Sign in with Google – Security bundle; Brand verification; Authentication policy
compliance), web.dev (Related Origin Requests), py_webauthn (PyPI/GitHub duo-labs).

## Sicherheit Agent ↔ Server

- Transport nur TLS: `kidctl enroll` verlangt `https://` (`--insecure-http` nur für Test-Server,
  wird in `device.json` vermerkt); der Agent baut ohne diesen Vermerk keine `ws://`-Verbindung auf.
  Zertifikatsprüfung über die System-CAs (websockets/urllib, Standard); unter Windows prüft Windows selbst
  (`truststore`), weil Python fehlende Stammzertifikate nicht nachladen kann.
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
- „Gerät entfernen“ in der Gerätedetailansicht (mit Bestätigung, auch für widerrufene Geräte): trennt eine laufende
  Verbindung, löscht Gerät, User-Zuordnungen und gemeldete Apps; das alte Token ist danach ungültig, der Rechner
  muss neu gekoppelt werden (sonst Agent deinstallieren). Nutzungsstatistik je Kind (`usage_record`, `app_usage`)
  bleibt erhalten (`device_id` → NULL); Sperrprotokoll, Spielsitzungen und Chat behalten ihre Einträge ohne Gerät.

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
- **Nutzungsbericht per E-Mail** (ergänzt 2026-10-20): je Elternteil im Profil wählbar (aus /
  wöchentlich / monatlich), braucht E-Mail-Adresse und SMTP. HTML-Mail (Tabellenlayout, Inline-Styles,
  Text-Alternative), gegliedert nach Kindern: Übersichtstabelle (ab 2 Kindern), Bildschirmzeit gesamt
  mit Veränderung ggü. Vorzeitraum, Ø pro Tag, Tage über dem Limit (aktuelles Limit inkl. Gutschriften,
  farbig **und** als Liste), Säulen je Tag, Top-5-Programme und -Spiele, gesperrte Aktionen (ohne „nur
  protokolliert“) mit Top 5, Verlängerungsanfragen, Gutschriften, Link zur Statistik. Versand ab 07:00
  Uhr montags (Vorwoche Mo–So) bzw. am 1. (Vormonat); verpasste Termine werden nachgeholt, solange der
  nächste Zeitraum nicht abgelaufen ist; `parent.report_sent` verhindert Doppelversand; nach
  Fehlschlag frühestens nach 1 h erneut. Beim Aktivieren gilt der letzte Zeitraum als gesendet.
  Profil: „Vorschau“ und „Jetzt senden“ (letzter abgeschlossener Zeitraum).
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

## E-Mails (entschieden 2026-11-19 per /grill-me)

- **Einheitliches HTML-Layout für alle Mails** – an Familien (Bestätigung, Passwort, Einladungen, Tickets,
  Abo-Hinweise, Rechnungen, Newsletter, Nutzungsbericht) und an Betreiber (Tickets, Profil, Alarme). Basis-Template
  `templates/mail/base.html` (Kopf mit Logo, Inhaltsblock, Fuß); jede Mail ist ein eigenes Template, das davon erbt.
  Gestaltung wie der bisherige Nutzungsbericht: Tabellenlayout, Inline-Styles, Material-3-Farben, dunkles Schema über
  `prefers-color-scheme`, max. 600 px, Vorschauzeile. Aktionslinks als Buttons, die auch in Outlook als Button
  erscheinen, darunter der Link als Text. **Textfassung bleibt** bei jeder Mail (multipart/alternative).
  Der Nutzungsbericht wird auf das Basis-Template umgestellt (Inhalt unverändert).
- **Logo als Inline-Anhang (CID):** Whitelabel-Logo, sonst KidCTL-Logo (aus `packaging/arch/kidctl.svg` als PNG
  gerendert). Für Mails auf max. 400 × 96 px verkleinert, zwischengespeichert, mit `branding.version` invalidiert.
  Keine externen Bilder, keine Data-URIs, kein Tracking.
- **Footer:** (1) Grund der Mail je Mailtyp, wo nötig mit Abmeldelink (Bericht, Newsletter); (2) Links
  *Neuigkeiten* · *Anleitungen* · *Support* (nur SaaS) · *Impressum* · *Datenschutz*; (3) Anbieter-Zeile (Name ·
  Anschrift · Kontakt-E-Mail); (4) „powered by KidCTL“ bei eigenem Namen.
- **Anbieter-Angaben:** neuer Block „Anbieter“ auf der Branding-Seite im Verwaltungsbereich (nur *Admin*,
  protokolliert): Name, Anschrift, Kontakt-E-Mail, URLs für Impressum, Datenschutz, Neuigkeiten (Vorgabe:
  Projektseite bzw. Wiki). Leere Felder entfallen im Footer (keine Platzhalter). **Einzelbetrieb:** Footer nur mit
  Grund und Links (Neuigkeiten = Projekt-News, Anleitungen = Wiki), Anbieterzeile nur, wenn ausgefüllt.
- **Newsletter:** Text in einer **Markdown-Teilmenge** (Überschriften `##`/`###`, fett/kursiv, Listen, Links,
  Trennlinie), serverseitig mit `markdown-it-py` (HTML aus, nur erlaubte Elemente, Links nur http/https/mailto) in
  HTML umgewandelt; Markdown-Quelle als Textfassung. Vorschau des HTML-Mails im Verwaltungsbereich vor dem
  Testversand.
- **Prüfung:** Tests für jeden Mailtyp in DE und EN (keine fehlenden Variablen, HTML < 100 KB wegen Gmail-Kürzung,
  CID-Logo vorhanden, Textfassung vorhanden, keine externen Ressourcen/Skripte, Whitelabel an/aus).
  Vorschauseite `/admin/mail-preview` (nur *Admin*): alle Mailtypen mit Beispieldaten, DE/EN umschaltbar,
  Button „Testmail an mich“ (an die eigene Betreiber-Adresse).
- **Umgesetzt (0.37.0):**
  - `app/mailer.py`: `render`/`send`, Logo, Fußzeile, Beispieldaten `samples()` für Vorschau und Tests.
  - `app/mdmail.py`: Markdown-Teilmenge.
  - Templates `templates/mail/*.html` erben von `mail/base.html`; Textfassung `mail/base.txt` aus denselben Variablen.
  - Anbieter-Felder in `branding` (Migration 0020).
  - Im abgeschotteten Vorschau-Rahmen kommt das Logo als `data:`-URI statt per CID: Die CSP erlaubt dort keine
    Server-Adressen.
  - Ausnahmen vom Basis-Layout: keine; der Nutzungsbericht nutzt eigene Blöcke (`content`, `footnote`).

## Backup (entschieden 2026-10-21)

- Täglich ab 03:00 (Server-Zeitzone) ein Schnappschuss per SQLite-Backup-API (konsistent auch während
  Schreibzugriffen), `integrity_check` auf der Kopie, gzip, Rechte 0600, atomar nach
  `<data>/backups/kidctl-JJJJ-MM-TT-HHMM.db.gz`; die neuesten 14 bleiben. Nach Fehlschlag frühestens nach
  einer Stunde erneut. CLI `app.cli backup`, WebUI „Einstellungen“ (vormals „Benachrichtigungen“) mit
  letztem Backup, Fehler und „Jetzt sichern“.
- Alarm per Push bei Fehlschlag oder ohne Backup seit 2 Tagen, einmal je Störung (erneut erst nach einem
  erfolgreichen Backup). Externe Sicherung von `./data` bleibt Aufgabe des Hosts.

## Verteilung & Updates

- Client als Split-PKGBUILD (`kidctl`, `kidctl-plasma`, `kidctl-gnome`): Agent, Tray-App, systemd-Unit, PAM-Konfiguration, nftables-Regeln,
  Firefox/Chromium-Policies, Mount-Konfiguration (`noexec`, Steam-Bind-Mount).
- Eigenes, GPG-signiertes Pacman-Repository als statisches Verzeichnis im Docker/Traefik-Stack.
- Updates über normales `pacman -Syu`.
- Umsetzung (2026-10-14, entschieden: lokal bauen, eigener Schlüssel): `packaging/repo/`
  - `kidctl-repo-key.sh`: Ed25519-Signierschlüssel in eigenem GPG-Ordner
    (`~/.local/share/kidctl-repo/gnupg`), Export des öffentlichen Schlüssels.
  - `kidctl-repo-build.sh`: `makepkg --sign` (mit `--nodeps`, reines Python) + `repo-add --sign` (alle
    Pakete des Split-PKGBUILD).
  - `kidctl-repo-publish.sh`: `rsync` nach `./data/repo` des Stacks; Server liefert es unter `/repo/`.
  - `kidctl-repo-client.sh`: Schlüssel holen, **gegen Fingerabdruck prüfen**, `pacman-key --lsign-key`,
    `[kidctl]` mit `SigLevel = Required DatabaseRequired` (Sicherung `pacman.conf.kidctl.bak`).
- Protokollversion im WebSocket-Handshake; Server unterstützt mindestens eine ältere Version.
  Zu alter Agent → Hinweis im WebUI, Durchsetzung läuft mit letzter Policy weiter.
- **Update per Knopf** (entschieden 2026-11-22 per /grill-me):
  - Geräteseite: Ist eine neuere Version in der Quelle des Geräts (`versions.update_for`), meldet der Agent die
    Fähigkeit `self_update` (`hello.features`) und ist er verbunden → Knopf „Update“ (POST
    `/devices/{id}/update`, CSRF, Familie). Offline: Knopf aus, Hinweis. Ältere Agenten: bisheriger Text
    (pacman/apt bzw. Installer). Geräteliste: Pill „Update: alt → neu“ bzw. „Update läuft“.
  - Protokoll (abwärtskompatibel, Version 1 bleibt): `update_now` (Server → Agent, **ohne Felder** – nie Paket,
    Quelle, URL oder Befehl), `update_status` (Agent → Server: `running` | `failed` | `current`, Meldung ≤ 300
    Zeichen). Erfolg = neue Version im nächsten `hello` (Paket startet den Agenten neu) → Stand `done`.
  - Entprellen: Server löst frühestens nach 10 min erneut aus, solange „läuft“ (nach Fehler/„aktuell“ sofort);
    Agent höchstens einmal je 60 s, nie parallel. „läuft“ > 15 min ohne neue Version → „Zeitüberschreitung“
    (erneut auslösbar). Status nur nach Auslösen übernommen; Meldung verschlüsselt (`device.update_detail`).
  - Agent Arch: eigene pacman-Konfiguration (`/run/kidctl/update/pacman.conf`) nur mit dem `[kidctl]`-Abschnitt –
    `Server` aus `/etc/pacman.conf` (nur http(s)), `SigLevel = Required DatabaseRequired` fest; `-Sy --needed`
    für `kidctl` und die installierten `kidctl-plasma`/`kidctl-gnome`. Andere Sync-Datenbanken bleiben unberührt
    (kein Teil-Upgrade-Zustand, kein Systemupdate).
  - Agent Debian/Ubuntu: `apt-get update` nur für `kidctl.sources` (muss `Signed-By` haben), dann
    `apt-get install --only-upgrade` (confold) für die installierten kidCTL-Pakete.
  - Linux: Paketmanager als transiente Unit `kidctl-update` (`systemd-run --wait`, max. 30 min) – das Paket startet
    den Agenten per `try-restart` neu, im Agenten-Dienst würde pacman/apt dabei mitten in der Transaktion beendet;
    dort gelten auch `ProtectSystem` usw. nicht. Fehlertext aus dem Journal der Unit.
  - Windows: vorhandener Selbst-Updater sofort (`Updater.run(force=True)`, Sperre `RETRY` übergangen), weiter mit
    Ed25519-Manifest und SHA-256.
  - Läuft sofort, auch während einer Kinder-Sitzung (Agent/Tray starten neu, gecachte Policy gilt weiter). Keine
    Mail/Push. Release zusammen mit der Info-Seite (0.46.0).

## Multi-Tenant / SaaS (entschieden 2026-11-15 per /grill-me, noch nicht umgesetzt)

Ziel: KidCTL als gehosteter Dienst (Software as a Service) für viele Familien, später mit Abrechnung.
Self-Hosting bleibt erhalten.

### Grundmodell
- **Eine Codebasis, zwei Betriebsarten:** `KIDCTL_MODE=single|saas`. Selbst gehostet = genau ein Mandant;
  Registrierung, Verwaltungsbereich, Abo und Support-Freigabe sind dort aus.
- **Mandant = Familie (Haushalt)**, Zielgruppe Privatkunden. Organisationen (Schulen, Einrichtungen) als Ebene über
  Familien sind bewusst nicht vorgesehen, aber nachrüstbar.
- **Mitgliedschaft:** Tabelle Account ↔ Familie ↔ Rolle. Vorerst gehört ein Account genau einer Familie
  (Mehrfach-Mitgliedschaft später ohne Datenmigration möglich).
- **Rollen:** *Inhaber* (genau einer: Abo, Familie löschen, Export/Import, Support-Zugang freigeben, Eltern
  einladen/entfernen, Inhaberschaft übertragen) und *Elternteil* (Kinder, Geräte, Regeln, Chat, Berichte). Neue Eltern
  per Einladungslink an ihre E-Mail; Passwort und Authenticator legen sie selbst fest. Rolle „nur lesen“ später.
- **Anmeldung mit E-Mail-Adresse** (systemweit eindeutig, Pflicht, mit Bestätigungslink); TOTP bleibt Pflicht.
  Benutzernamen entfallen, Anzeigename bleibt. Bestehende Accounts ohne E-Mail melden sich bis zum Nachtragen mit dem
  Benutzernamen an; im Einzelbetrieb bleibt die E-Mail optional. Kinder- und Gerätenamen sind nur innerhalb der Familie
  eindeutig.

### Datentrennung und Verschlüsselung
- **Eine Datenbank, `family_id` an allen familienbezogenen Tabellen.** Zentrale Pflicht-Filterung über den
  Sitzungs- bzw. Aufgabenkontext (eine Abfrage ohne Familienkontext schlägt fehl), dazu Tests, die über jede Route
  gezielt fremde Daten abzurufen versuchen. Row-Level Security in PostgreSQL als mögliche dritte Schicht.
- **Feldverschlüsselung je Familie:** eigener Datenschlüssel je Familie, gesichert mit einem Hauptschlüssel aus der
  Umgebung (später Schlüsseldienst). AES-GCM mit `family_id`, Tabelle und Spalte als zusätzlichen Prüfdaten.
  Entschlüsselt wird immer mit dem Schlüssel des aktuellen Kontexts, nie mit dem der gelesenen Zeile – ein Programmfehler,
  der eine fremde Zeile lädt, führt zu „Entschlüsselung fehlgeschlagen“ statt zur Anzeige fremder Daten.
  Verschlüsselt: Chat, Namen von Kindern/Geräten/Benutzern, Programm- und Website-Namen in Protokoll und Statistik,
  E-Mail-Adressen der Eltern. Unverschlüsselt: Zahlen, Zeitstempel, IDs; öffentliche Sperrlisten.
  Folgen: Suche, Filter und Sortierung über verschlüsselte Felder laufen im Server über die Daten einer Familie.
- **Grenzen:** Der Server muss ohne angemeldete Eltern arbeiten (Policy, Alarme, Berichte, Push) und hält daher alle
  Schlüssel – kein Schutz gegen den Betreiber oder einen übernommenen Server, wohl aber gegen versehentliche
  Fremdanzeige und gestohlene Datenbank-Kopien.
- **Löschen per Schlüssel:** Familie löschen = Datenschlüssel verwerfen; damit sind ihre Daten auch in alten Backups
  unlesbar. Der Hauptschlüssel ist das kritischste Geheimnis und wird getrennt gesichert.

### Datenbank und Größe
- **PostgreSQL in beiden Betriebsarten**, Migrationen mit Alembic statt `app/migrate.py` (CLAUDE.md wird angepasst;
  Migrationen weiterhin gegen eine Alt-Schema-Datenbank testen). Einmaliges Übernahmeskript SQLite → PostgreSQL.
- Ausgelegt auf etwa 5.000 Familien / 20.000 Geräte mit einem Server-Prozess; mehrere Prozesse (gemeinsamer
  Nachrichtenkanal für WebSockets) erst bei Bedarf.
- Plattformweit einmal: Server-Adresse, Paket-Repo, GPG- und Windows-Update-Schlüssel, VAPID, Mailversand,
  Sperrlisten. Der Kopplungscode ordnet ein Gerät seiner Familie zu.

### Registrierung und Betreiber
- **Registrierung:** `REGISTRATION=open|invite|closed`, SaaS-Start auf `invite`, Einzelbetrieb `closed` (erster
  Account per CLI wie heute). Ablauf: E-Mail → Bestätigung → Passwort + Authenticator → Familie → erstes Kind →
  Kopplungscode. Begrenzung je IP; CAPTCHA erst bei Spam und ohne Dienste von Dritten.
- **Verwaltungsbereich des Betreibers:** eigene Rolle, getrennt von Familien-Accounts, TOTP-Pflicht, optional
  IP-Beschränkung. Nur Metadaten (Familien, Anzahl Kinder/Geräte, Agent-Versionen, letzte Aktivität, Abo-Status,
  Fehler) – ohne Familienkontext und damit ohne Entschlüsselung.
- **Support-Freigabe:** Eltern (Inhaber) geben Einsicht zeitlich begrenzt (z. B. 24 h) frei, jederzeit widerrufbar;
  jeder Zugriff wird protokolliert und ist für die Familie sichtbar.
- **Festgelegt 2026-11-17 (Etappe 4):**
  - Support-Ansicht **nur lesend**: Der Betreiber sieht die Seiten der Familie wie ein Elternteil, kann aber nichts
    ändern oder senden; Banner „Support-Ansicht“.
  - **Eigene Betreiber-Accounts** (`cli create-operator`), Anmeldung unter `/admin` mit Passwort + TOTP, eigene
    Sitzung, optional nur aus bestimmten IP-Netzen (`ADMIN_ALLOW_NETS`). Getrennt von Eltern-Accounts.
  - **Einladungscodes** für `REGISTRATION=invite` erzeugt der Betreiber im Verwaltungsbereich: einmalig nutzbar,
    30 Tage gültig, mit Notiz; eingelöste Codes sichtbar.
  - **Eltern einladen** (Inhaber): Einladungslink an eine E-Mail-Adresse (7 Tage gültig, wird angezeigt und – mit
    SMTP – per Mail geschickt); die eingeladene Person legt Passwort und Authenticator selbst fest. Ersetzt das
    Anlegen mit Passwort durch andere Eltern, auch im Einzelbetrieb.

### Abrechnung (Vorbereitung)
- Später pauschal pro Familie mit Stufen nach Kinderzahl, Testphase (z. B. 30 Tage), Jahresabo mit Rabatt.
- Jetzt nur: Tarif, Abo-Status, Testphase je Familie, Grenzprüfung beim Anlegen eines Kindes, neutrales Feld für
  Zahlungsanbieter und Kundennummer. Anbindung später über einen Webhook „aktiv/gekündigt/überfällig“; Anbieter offen
  (voraussichtlich Merchant of Record, nach Klärung mit dem Steuerberater). Im Einzelbetrieb keine Grenzen.
- **Ablauf von Testphase oder Abo:** 14 Tage Schonfrist mit Hinweisen (Mail, Push, Banner). Danach Ruhemodus: WebUI
  nur Abo, Export, Löschung; Agenten erhalten den Zustand „pausiert“ und heben alle Einschränkungen auf, Tray zeigt
  „KidCTL pausiert“; nach erneuter Zahlung gelten die Regeln sofort wieder. Nach weiteren 90 Tagen ohne Abo Löschung
  der Familie mit Vorwarnung. Kinder werden nie ohne ansprechbare Eltern eingeschränkt.
- **Festgelegt 2026-11-17 (Etappe 5):**
  - Vorerst **ein Tarif „Standard“ ohne Kindergrenze**; die Grenzprüfung beim Anlegen eines Kindes ist vorbereitet
    (Grenze je Tarif), Stufen kommen mit den Preisen.
  - Tarif **„Kostenlos“** ohne Ablauf und Grenze: bestehende Familien bei der Migration, vom Betreiber im
    Verwaltungsbereich setzbar und per Einladungscode mitgebbar. Neue Familien sonst: Testphase 30 Tage
    (`KIDCTL_TRIAL_DAYS`). Einzelbetrieb: immer kostenlos, keine Fristen.
  - Abo-Status **manuell im Verwaltungsbereich** und über einen neutralen **Webhook** (`BILLING_WEBHOOK_SECRET`,
    Status aktiv/gekündigt/überfällig, bezahlt bis, Anbieter, Kundennummer) für den späteren Zahlungsanbieter.
  - Schonfrist-Hinweise per Mail und Push 14, 7 und 1 Tag vor dem Ruhemodus, Banner im WebUI. Im Ruhemodus nur
    Einstellungen (Abo, Export, Löschen), Profil und Abmelden.
  - **Automatische Löschung** 90 Tage nach Beginn des Ruhemodus; Vorwarnung per Mail und Push 30, 7 und 1 Tag vorher.
- **Festgelegt 2026-11-17 (Familienname, Rechnungsangaben):** Den Namen der Familie setzt der Inhaber in den
  Einstellungen, der Betreiber sieht und ändert ihn im Verwaltungsbereich. Rechnungsangaben (Name bzw. Firma, Straße,
  PLZ, Ort, Land, E-Mail für Rechnungen, optional USt-IdNr.) hinterlegt der Inhaber selbst; gebraucht ab bezahltem
  Tarif bzw. in den letzten 7 Tagen der Testphase (Hinweis, Banner, Markierung beim Betreiber). Beides mit einem
  Plattform-Schlüssel (HKDF aus dem Hauptschlüssel) verschlüsselt: für den Betreiber lesbar – Ausnahme vom Grundsatz
  „nur Metadaten“ –, nicht in einer Datenbankkopie allein; mit der Familie gelöscht, nicht im Familien-Export.

### Verwaltungsbereich 2 (entschieden 2026-11-18 per /grill-me, noch nicht umgesetzt)

Ziel: Verwaltungsbereich bei vielen Familien handhabbar und gut lesbar; Support-Tickets; manuelle Rechnungen.

#### Betreiber-Rollen und Verwaltungsprotokoll
- **Rollen:** *Admin* (alles) und *Support* (Familien sehen, Tickets bearbeiten; kein Abo ändern, keine Rechnungen,
  keine Einladungscodes, keine Rechnungs-Einstellungen). Rolle je Betreiber-Account; anlegen/ändern per CLI
  (`create-operator --role admin|support`, `set-operator-role`). Bestehende Accounts werden bei der Migration *Admin*.
  Rollenprüfung zentral (Abhängigkeit je Route), nicht im Template.
- Betreiber-Accounts bekommen eine **E-Mail-Adresse** und den Schalter **„Ticket-Mails erhalten“** (im eigenen
  Profil unter `/admin`). Optional `SUPPORT_NOTIFY_EMAIL` (Sammeladresse, erhält alle Ticket-Benachrichtigungen).
- **Verwaltungsprotokoll:** jede Änderung durch Betreiber oder Webhook an einer Familie bzw. Plattform-Einstellung:
  Zeit, Betreiber (bzw. „Webhook“/„System“), Familie, Objekt, Feld alt → neu. Erfasst: Familienname, Abo-Felder,
  Ticket-Status, Rechnungen (angelegt, ausgestellt, bezahlt, storniert, erneut gesendet), Rechnungs-Einstellungen,
  Preisliste. Anzeige als Karte „Verlauf“ in der Familien-Detailansicht und unter `/admin/log` (filterbar). Für die
  Familie nicht sichtbar. Aufbewahrung 2 Jahre; Einträge zu Rechnungen so lange wie die Rechnung.

#### Übersicht (`/admin`)
- **Kennzahl-Kacheln** (Klick = Filter): Familien · Kinder · Geräte · in Testphase · Testphase endet ≤ 7 Tage ·
  im Ruhemodus · offene Tickets · letztes Backup (grün/rot).
- **Suchzeile:** Freitext (ID, Familienname; Teilwort, ohne Groß/Klein) und Filter-Chips: Status (Testphase, aktiv,
  überfällig, gekündigt, Ruhemodus), Tarif (Kostenlos, Standard), Probleme (Geräte offline, Agent veraltet,
  Rechnungsangaben fehlen, offenes Ticket, Support-Freigabe aktiv).
- **Tabelle**, jede Zelle einzeilig, alle Spalten sortierbar: **ID** · Familie · Seit · Kinder / Geräte · Agent
  (älteste Version, ⚠ wenn veraltet) · Letzte Aktivität (relativ, genaues Datum als `title`; jüngere von Gerät und
  Eltern) · Abo (Tarif · Status · Datum) · Zustand (Abzeichen). Spalte „Eltern“ entfällt. Mobil als Karten.
- **Umsetzung im Server:** Metadaten aller Familien laden, Namen entschlüsseln, filtern, sortieren, 50 je Seite;
  Parameter in der URL (`q`, `sort`, `status`, `plan`, `issue`, `page`), lesezeichenfähig, ohne JavaScript.
  Standard-Sortierung: ID aufsteigend.

#### Familien-Detailansicht (`/admin/families/<id>`)
- **Karten-Raster** (Material 3, mobil einspaltig): Kopf (ID, Name, Status-Abzeichen, seit) · **Abo** · **Rechnungsangaben**
  (nur lesen, gepflegt von der Familie; Markierung wenn fehlend) · **Nutzung** (Eltern, Kinder, Geräte mit Plattform,
  Agent-Version, zuletzt gesehen – nur Metadaten) · **Support** (Freigabe, Zugriffsprotokoll, Tickets der Familie) ·
  **Rechnungen** · **Verlauf**.
- **Bearbeiten pro Karte:** änderbare Karten (Name, Abo) zeigen zunächst nur Werte und einen Button „Bearbeiten“;
  `?edit=<karte>` rendert die Karte als Formular mit „Speichern“/„Abbrechen“, eigenes Formular mit CSRF. Hinweis bei
  Abo-Änderungen bleibt („Wirkt sofort …“). Rolle *Support* sieht keinen Bearbeiten-Button.

#### Tickets
- **Nur im SaaS-Betrieb.** Eltern-WebUI: neuer Menüpunkt **„Support“** (auch im Ruhemodus erreichbar). Alle Eltern
  der Familie dürfen Tickets anlegen und sehen alle Tickets der Familie.
- **Ticket:** Betreff, Kategorie (*Technik*, *Abo & Rechnung*, *Sonstiges*), Nachrichtenverlauf; keine Anhänge.
  Beim Anlegen automatisch angehängte Metadaten (für Support sichtbar): Agent-Versionen, Plattformen der Geräte,
  Abo-Status, Sprache.
- **Status:** *neu*, *in Bearbeitung*, *wartend*, *geschlossen*.
  - Familie legt an → *neu*.
  - Support antwortet → *wartend* (beim Antworten wählbar: *in Bearbeitung*).
  - Familie antwortet → *in Bearbeitung*; Antwort auf *geschlossen* öffnet wieder, nur bis 14 Tage nach Schließen
    (danach Hinweis „bitte neues Ticket“).
  - *wartend* ohne Antwort der Familie 14 Tage → *geschlossen* (Hinweis an die Familie).
  - Familie kann ihr Ticket schließen; Support kann jeden Status setzen.
- **Interne Notizen:** Support-Nachrichten mit „intern“ sieht nur der Support (farblich abgesetzt).
- **Benachrichtigungen:** nur Hinweis + Link, **ohne Inhalt**. Neues Ticket / Antwort der Familie → Betreiber mit
  „Ticket-Mails“ und `SUPPORT_NOTIFY_EMAIL`. Antwort des Supports / automatisches Schließen → alle Eltern der Familie
  per Mail und Push. Interne Notizen lösen nichts aus.
- **Verwaltung:** Reiter **„Tickets“** neben „Familien“ und „Einladungscodes“: Liste im Server sortier-/filterbar
  (Status, Kategorie, Familie, Freitext im Betreff), Standard „offen“ (alles außer *geschlossen*), älteste zuerst;
  Ticketseite mit Verlauf, Antwortfeld, intern-Schalter, Statusauswahl, Link zur Familie, „zuletzt bearbeitet von“.
- **Speicherung:** Betreff und Nachrichten mit dem **Plattform-Schlüssel** (HKDF aus dem Hauptschlüssel, wie
  Rechnungsangaben) – zweite dokumentierte Ausnahme von „nur Metadaten“. Status, Kategorie, Zeitstempel, IDs
  unverschlüsselt. Hinweis im Formular: „Diese Nachricht kann der Support lesen. Für Einsicht in deine Einstellungen
  gib zusätzlich eine Support-Freigabe.“
- **Aufbewahrung:** geschlossene Tickets 12 Monate nach Schließen (`TICKET_RETENTION_DAYS`), mit der Familie gelöscht,
  nicht im Familien-Export.

#### Rechnungen
- **Grundsatz:** Merchant of Record oder Zahlungsdienstleister ist offen (Steuerberater). Jetzt **manueller
  Generator** nach den Pflichtregeln (§ 14 UStG); Automatik per Webhook erst nach der Entscheidung.
- **Steuermodus** `INVOICE_TAX_MODE=small_business|standard` (Standard `small_business`): Kleinunternehmer ohne
  USt-Ausweis mit Hinweis „Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.“; Regelbesteuerung Netto + 19 % USt +
  Brutto mit Steuersatz und -betrag. Modus wird **je Rechnung gespeichert**. Nur Kunden in Deutschland (kein OSS),
  Ausgabe PDF (keine E-Rechnung).
- **Nummernkreis** `INVOICE_NUMBER_FORMAT` (Standard `RE-{YYYY}-{NNNN}`): fortlaufend, lückenlos, je Jahr ab 0001,
  Vergabe erst beim Ausstellen, transaktionssicher (Sperre/Sequenz). Storno-Rechnungen im selben Kreis.
- **Fälligkeit** `INVOICE_DUE_DAYS` (Standard 14).
- **Einstellungen → Rechnungen** (nur *Admin*, in der Datenbank, protokolliert – Ausnahme von „Konfiguration nur über
  `.env`“): Absender (Name/Firma, Anschrift, Steuernummer oder USt-IdNr., Kontakt), Bankverbindung (Inhaber, IBAN,
  BIC), Fußzeile/Zahlungshinweis, **Preisliste** (Bezeichnung, Betrag, Laufzeit in Monaten, aktiv). Ausstellen erst
  möglich, wenn alle Pflichtfelder gesetzt sind.
- **Erstellen** (Detailansicht → „Rechnung erstellen“, nur *Admin*): Vorlage aus der Preisliste wählen → Position und
  Leistungszeitraum vorausgefüllt (Beginn = Tag nach „bezahlt bis“, sonst heute; Ende = Beginn + Laufzeit − 1 Tag);
  Positionen frei änderbar und ergänzbar (Text, Menge, Einzelpreis; negative Beträge für Rabatt). Rechnungsadresse
  aus den Rechnungsangaben der Familie; fehlen sie, kein Ausstellen.
- **Lebenszyklus:** *Entwurf* (änderbar, löschbar, keine Nummer) → **Ausstellen** (Nummer, Rechnungsdatum, Kopie der
  Rechnungsadresse, Beträge und Steuermodus eingefroren, PDF erzeugt und gespeichert, unveränderlich) → *offen* →
  *bezahlt* (mit Zahlungsdatum). **Storno** nur per Storno-Rechnung (eigene Nummer, Bezug auf Original, negative
  Beträge); Original bleibt.
- **Kopplung mit dem Abo:** „Als bezahlt markieren“ setzt Abo-Status *aktiv* und „bezahlt bis“ auf das Ende des
  Leistungszeitraums, falls später als der bisherige Wert; ein Ruhemodus endet sofort (Policy an die Agenten). Storno
  einer bezahlten Rechnung ändert das Abo nicht, sondern zeigt „Abo prüfen“. Alles im Verwaltungsprotokoll.
  Dieselbe Logik nutzt später der Webhook.
- **Versand:** beim Ausstellen Mail an die Rechnungs-E-Mail der Familie mit PDF-Anhang und Zahlungsinformationen
  (Betrag, Fälligkeit, IBAN, Verwendungszweck = Rechnungsnummer); „Erneut senden“ im Verwaltungsbereich. Eltern sehen
  ihre Rechnungen unter *Einstellungen → Abo* (Nummer, Datum, Betrag, Status, PDF).
- **Archiv und Aufbewahrung:** Rechnungsadress-Kopie und PDF mit dem **Plattform-Schlüssel**; Rechnungen hängen nicht
  am Familienschlüssel und **überleben das Löschen der Familie** (Bezug wird „gelöschte Familie #<id>“). Automatische
  Löschung nach Ablauf der Aufbewahrungsfrist: 8 Jahre ab Ende des Ausstellungsjahres (§ 14b UStG, § 147 AO; Frist
  als Konstante). Nicht im Familien-Export.
- **Rechnungskopf mit Erscheinungsbild** (umgesetzt in 0.45.0, entschieden 2026-11-20): Kopfzeile des PDFs mit
  **Logo** und **Name des Dienstes** aus *Erscheinungsbild* (Whitelabel); ohne Logo nur der Name, mit „Logo enthält
  den Namen“ nur das Logo. Logo links oben (10–28 mm von oben, höchstens 18 mm hoch und 60 mm breit,
  Seitenverhältnis bleibt; gespeichert ist es immer als PNG, SVG wird beim Hochladen nicht angenommen), Name daneben,
  zu lange Namen werden gekürzt. Gilt nur für neu ausgestellte
  Rechnungen: das PDF wird beim Ausstellen erzeugt und gespeichert, ausgestellte Rechnungen und ihre PDFs werden nie
  neu gerendert. Absenderangaben bleiben unverändert im Kopf. Muster über `dev/sample_invoices.py` prüfen.
- **`/admin/invoices`** (nur *Admin*): alle Rechnungen, filterbar (Jahr, Status, Familie), Summen je Jahr; **Export
  je Jahr** als CSV (Nummer, Datum, Familie, Leistungszeitraum, Netto/USt/Brutto, Status, Zahlungsdatum, Storno-Bezug)
  und ZIP mit allen PDFs.
- **PDF** mit ReportLab (keine Systembibliotheken im Image); Layout schlicht, DIN-5008-nah; Sprache Deutsch.

### Missbrauch
- Nur offener Betrieb: Tray-Symbol auf jedem verwalteten Account sichtbar, Hinweis beim ersten Anmelden nach der
  Zuordnung („Dieser Account wird von KidCTL verwaltet. Erfasst wird: …“), unter Windows Eintrag als installiertes
  Programm – nicht abschaltbar; kein verdeckter Modus. Nutzungsbedingungen: nur für eigene bzw. sorgeberechtigte
  minderjährige Kinder; Verstoß → Sperre der Familie. AGB, Datenschutzerklärung und Impressum anwaltlich prüfen.

### Datenschutz, Backup, Aufbewahrung
- **Export/Import je Familie** (Inhaber): ZIP mit JSON/CSV in versioniertem Format, entschlüsselt mit dem
  Familienschlüssel; Import auch für den Wechsel zwischen Self-Hosting und SaaS. Der heutige Backup-Download der ganzen
  Datenbank für Eltern entfällt im SaaS (enthält alle Familien) und bleibt nur im Einzelbetrieb.
- **Plattform-Backup:** `pg_dump` täglich, verschlüsselt, an einen zweiten Ort; Hauptschlüssel getrennt gesichert;
  Status im Verwaltungsbereich ohne Download. Wiederherstellung einzelner Familien per Betreiber-Skript.
- **Aufbewahrung (fest, per Umgebung änderbar):** Protokoll gesperrter Aktionen 90 Tage; Spielsitzungen und
  Programmnutzung 90 Tage; Bildschirmzeit je Tag 13 Monate; Chat 12 Monate (Eltern können ihn jederzeit leeren);
  Anmeldeversuche 30 Tage; abgelaufene Sitzungen/Tokens 30 Tage; Protokoll der Support-Zugriffe 2 Jahre; gelöschte
  Familien sofort (Schlüssel verworfen).
- Ergänzt 2026-11-18: Tickets 12 Monate nach Schließen; Verwaltungsprotokoll 2 Jahre; **Rechnungen 8 Jahre** ab Ende
  des Ausstellungsjahres – sie bleiben nach dem Löschen einer Familie erhalten (gesetzliche Aufbewahrungspflicht,
  Art. 17 Abs. 3 lit. b DSGVO) und werden danach automatisch gelöscht. Datenschutzerklärung entsprechend ergänzen.

### Hosting und Umzug
- Start auf srv02 unter `kids.nossco.de`; die bestehende Installation wird erster Mandant (bisherige Eltern:
  der erste Account Inhaber, weitere Elternteile). Erste Familien: Freunde und Verwandte. Umzug auf eigenen Server und
  Produkt-Domain später.
- **Umzugsnachricht im Protokoll** (jetzt einbauen): Der Server teilt dem Agenten eine neue Adresse mit; der Agent
  übernimmt sie nur, wenn er sich dort mit seinem Geräte-Token erfolgreich anmeldet, sonst bleibt er beim alten.

### Etappen
1. **Fundament:** PostgreSQL + Alembic, Familie/Mitgliedschaft/Rollen, `family_id` mit Pflicht-Filterung und
   Isolationstests, E-Mail-Anmeldung, `KIDCTL_MODE`, Übernahme der bestehenden Installation.
2. **Verschlüsselung und Fristen:** Datenschlüssel je Familie, Feldverschlüsselung, Löschen per Schlüssel, Suche und
   Sortierung im Server, Aufbewahrungsfristen, „Chat leeren“.
3. **Agent-Version:** Umzugsnachricht, Zustand „pausiert“, Hinweis beim ersten Anmelden.
4. **SaaS-Betrieb:** Registrierung (`invite`), Einladungen, Verwaltungsbereich mit Support-Freigabe, Export/Import,
   Backup-Seite aufgeteilt.
5. **Abo-Vorbereitung:** Tarife, Testphase, Grenzen, Schonfrist/Ruhemodus/Löschung, Feld für Zahlungsanbieter.

Regel: Keine fremde Familie auf dem Server vor Abschluss von Etappe 4.

## Projekthomepage (entschieden 2026-11-16 per /grill-me, umgesetzt 2026-11-16; Livegang offen)

Ziel: kleine Projektseite, die kidCTL vorstellt, die Funktionen mit Screenshots zeigt und einen Roadmap- und
News-Bereich hat, den Claude Code pflegt.

### Zielgruppe und Ort
- **Zielgruppe jetzt:** Self-Hoster und Technikaffine. Struktur lässt Platz für spätere SaaS-Bereiche
  („Gehostet nutzen“, Preise), die erst nach SaaS-Etappe 4 kommen.
- **Hosting:** GitHub Pages, Quellcode in `website/` im kidCTL-Repo.
- **Adresse:** `https://kidctl.de/` (seit 2026-11-21; vorher `https://adriannoss.github.io/kidCTL/`, GitHub leitet
  weiter). Domain in den Pages-Einstellungen des Repos (Veröffentlichung per Actions, keine `CNAME`-Datei) und
  `baseURL` in der Hugo-Konfiguration. SaaS später unter `app.`/`my.` derselben Domain.
- **Sprache:** Deutsch und Englisch mit Umschalter; Englisch Standard (`/`), Deutsch unter `/de/`.

### Technik und Design
- **Hugo** (Extended nicht nötig), eigenes schlankes Theme in `website/themes/kidctl/` (kein Fremd-Theme,
  kein Node/npm). Mehrsprachigkeit und RSS über Hugo-Bordmittel.
- **Design im Material-3-Stil der WebUI:** Farben, Schrift, Radien aus `server/app/static/style.css`
  übernommen; mobile first; hell/dunkel automatisch über `prefers-color-scheme`. JavaScript nur für
  Screenshot-Galerie (Lightbox) und ggf. Sprachweiche – Seite funktioniert ohne JS.
- **Kein Tracking, keine externen Requests:** keine Analytics, keine Cookies, Schriften und Icons selbst gehostet,
  kein CDN. Grobe Zahlen über GitHub-Traffic und Release-Downloads.

### Aufbau
- **Startseite (One-Pager):** Hero (Claim, „deine Daten auf deinem Server“, Buttons *Installieren* und *GitHub*) →
  Plattformen (Arch, Debian/Ubuntu mit KDE Plasma/GNOME Wayland; Windows 10 22H2 und 11 ab 23H2, x64) →
  Funktionen mit Screenshots → So funktioniert's (Server, Agent, Tray als einfaches Schema) → Datenschutz &
  Sicherheit → Roadmap (kompakt) → letzte 3 News.
- **Schnellstart** (Abschnitt bzw. Anker *Installieren*): Kurzfassung wie im README, DE/EN; für Details Link auf
  `docs/installation.md` und `docs/haertung.md` bei GitHub. `docs/` wird nicht in die Seite eingebunden, solange
  es nur auf Deutsch vorliegt.
- **News:** Archivseite mit RSS-Feed je Sprache, eigene Seite je News.
- **Releases:** automatisch erzeugte Liste aller Versionen (siehe unten).
- **Impressum / Datenschutz:** Datenschutz beschränkt sich auf Server-Logs von GitHub Pages. Impressum mit
  Platzhaltern (`TODO-IMPRESSUM`); ein Build-Check lässt den Deploy fehlschlagen, solange ein Platzhalter enthalten
  ist. Adresse klärt Adrian beim Domain-Kauf; bei SaaS ein Impressum-/Adressdienst.

### News
- Eine News je **Minor-Version** (x.Y.0), je **sicherheitsrelevantem Patch** und auf ausdrücklichen Wunsch
  (Meilensteine ohne Release, z. B. „SaaS-Etappe 1 abgeschlossen“). Normale Patches nur in der Release-Liste.
- Format: `website/content/news/YYYY-MM-DD-<slug>.de.md` und `.en.md`, Frontmatter `title`, `date`, optional
  `version`, `summary`. Für Nutzer geschrieben (was ist neu, was bringt es), nicht wie der technische Changelog;
  3–8 Sätze, ggf. Screenshot.
- **Start:** 4–6 nachträgliche Meilenstein-News mit echtem Datum aus dem Changelog (z. B. erster lauffähiger Stand,
  Debian/Ubuntu + GNOME, Spiele-Limit, Windows-Unterstützung, WebUI im Material-3-Stil) plus „Projektseite online“.
  Auswahl vor dem Schreiben mit Adrian abstimmen.

### Roadmap
- Quelle: `website/data/roadmap.yaml`; je Eintrag `id`, `title` und `text` je Sprache (`de`/`en`), `status`
  (`planned` | `in_progress` | `done`), optional `version`. Keine Termine.
- Gepflegt von Claude Code, sobald in `spec.md` ein Feature entschieden, begonnen oder abgeschlossen wird. Nur
  öffentlich Sinnvolles: keine Abrechnungs-, Missbrauchs- oder Rechtsdetails.
- **SaaS:** ein Eintrag „Gehostete Variante für Familien“ (`planned`), ohne Termine und Preise, mit dem Satz
  „Self-Hosting bleibt vollständig und kostenlos (AGPL-3.0)“.

### Screenshots
- **WebUI automatisch:** `dev/screenshots.py` – legt mit einem Seed-Skript fiktive Demo-Daten an (Kinder z. B.
  „Lena“ und „Max“, Geräte Linux/Windows, Statistik über 14 Tage, Spiele, Chat, gesperrte Aktionen, Anfragen),
  startet den Dev-Server mit eigenem Datenverzeichnis (nicht `~/.local/share/kidctl-dev`), meldet sich wie
  `dev/webctl.py` mit TOTP an und fotografiert per Playwright die wichtigsten Seiten:
  mobil (z. B. 390×844) und Desktop (1280×800) × hell/dunkel × DE/EN.
  Ausgabe nach `website/static/img/screenshots/<lang>/<theme>/<name>-<mobile|desktop>.webp`.
  Keine echten Daten, keine echten Namen/Domains.
- **Client-Seite einmalig von Hand** aus den Test-VMs: Tray-App, Warnung vor Zeitende, Sperrbildschirm,
  Anfrage-Dialog, Windows. Ablage `website/static/img/client/`.
- Die Seite zeigt Screenshots passend zum Farbschema (`<picture>` mit `media="(prefers-color-scheme: dark)"`).

### Releases
- `packaging/repo/kidctl-release.sh --apply` legt als letzten Schritt das **GitHub-Release** `v<Version>` an
  (`gh release create`): Windows-Installer als Asset, SHA-256 des Installers, Link zum Build-Lauf, Kurztext aus dem
  Changelog. Dry-Run zeigt den Befehl nur an. Damit stimmt auch die Aussage im README (Code signing policy).
- Der Pages-Build liest die Releases per GitHub-API (`gh api repos/AdrianNoss/kidCTL/releases`) in
  `website/data/releases.json` und rendert daraus die Release-Liste.

### Veröffentlichung
- Workflow `.github/workflows/pages.yml`: baut und veröffentlicht bei Push auf `main` mit Änderungen unter
  `website/**`, bei `release: published` und von Hand (`workflow_dispatch`). Hugo-Version im Workflow fest
  gepinnt. Schritte: Releases holen → Platzhalter-Check (Impressum) → `hugo --minify` → Deploy auf Pages.

### Wiki (entschieden 2026-11-17 per /grill-me, umgesetzt 2026-11-17)

Ziel: Die Anleitungen aus `docs/` als gut lesbares Wiki auf der Projektseite, zweisprachig, mit Suche.
Quelle bleiben die Markdown-Dateien im Repo; auf GitHub sind sie weiterhin direkt lesbar.

#### Technik
- Im bestehenden Hugo-Theme. `docs/` wird per Hugo-Mount als Abschnitt eingebunden: `/docs/` (EN),
  `/de/docs/` (DE).
- **Dateien:** `docs/<name>.md` = Englisch, `docs/<name>.de.md` = Deutsch (wie `README.md`/`README.de.md`).
  Dateinamen englisch.
- **Reines Markdown ohne Frontmatter.** Titel = erste `#`-Überschrift. Gruppen und Reihenfolge zentral in
  `website/data/docs.yaml` (Gruppentitel `de`/`en`, Liste von Dateinamen ohne Endung).
- **Render-Hooks:** relative Links zwischen `docs/`-Dateien → Wiki-Links der gleichen Sprache; Links außerhalb
  von `docs/` (z. B. `../packaging/...`) → GitHub-Links (`blob/main/...`); Bilder aus `docs/img/` werden
  mitveröffentlicht.
- **Hinweisboxen** über GitHub-Alert-Syntax (`> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]`) –
  auf GitHub und im Wiki gleich.

#### Aufteilung (`docs/`)
| Gruppe | Seiten (Reihenfolge) |
|---|---|
| Erste Schritte / Getting started | `installation` (Übersicht: Voraussetzungen, Reihenfolge, Links), `server`, `package-repo`, `child-linux`, `child-windows`, `webui` |
| Betrieb / Operation | `updates`, `backup`, `postgresql-migration`, `uninstall`, `games` (Steam, Heroic) |
| Sicherheit / Security | `hardening` (bisher `haertung.md`), `limitations` (siehe „Sicherheitsrichtlinie“), `security` (Mount von `SECURITY.md`/`SECURITY.de.md`), `code-signing` |

- `installation.md` und `code-signing.md` behalten ihren Pfad (externe Links, SignPath).
- Der Bedien-Teil aus `packaging/repo/README.md` wandert nach `docs/package-repo`, der aus
  `packaging/windows/README.md` nach `docs/child-windows`. Die Komponenten-READMEs (`server/`, `agent/`, `tray/`,
  `packaging/*`, `dev/vm/`) behalten nur Entwickler-Interna, bleiben deutsch und verweisen fürs Bedienen ins Wiki.

#### Sprache und Parität
- **Quellsprache Deutsch.** Änderungen werden auf Deutsch geschrieben und im selben Commit ins Englische
  übersetzt; bei Widerspruch gilt die deutsche Fassung.
- Beide Fassungen haben **exakt den gleichen Inhalt** – gilt für alle `docs/`-Paare, für
  `README.md`/`README.de.md` und für `SECURITY.md`/`SECURITY.de.md`. Spec, `CLAUDE.md`, Komponenten-READMEs und Code-Kommentare bleiben deutsch.
- **Paritäts-Check** `dev/check_docs_parity.py` (als pytest-Test und im Pages-Workflow vor dem Hugo-Build;
  Abweichung → Fehler mit Datei und Stelle):
  - jede `x.md` hat eine `x.de.md` und umgekehrt
  - gleiche Überschriftenfolge (Anzahl und Ebenen)
  - Codeblöcke identisch (Sprache des Blocks und Inhalt; Kommentarzeilen `#`/`//`/`REM` dürfen abweichen)
  - gleiche Anzahl Tabellenzeilen, Listenpunkte und Hinweisboxen (je Typ)
  - gleiche Linkziele (Anker dürfen sprachabhängig sein) und gleiche Bilder (bis auf den Sprachordner
    `img/en/` ↔ `img/de/`)
  - jede `docs/`-Datei steht in `website/data/docs.yaml`, jeder Eintrag dort existiert

#### Suche
- **Pagefind** über die ganze Seite (Wiki, News, Startseite), Sprachen getrennt über `lang`, Filter
  „Wiki“/„News“. Suchfeld im Seitenkopf, Tastenkürzel `Strg+K` und `/`. Index wird erst beim Tippen geladen;
  keine externen Requests. Installation im Workflow per `pip install pagefind` (kein Node), Version gepinnt.
  Lokal: `hugo --minify && python -m pagefind --site public`.

#### Layout
- Linke Navigation mit Gruppen aus `docs.yaml` (mobil einklappbar), Inhaltsverzeichnis rechts (H2/H3),
  Kopier-Button an Codeblöcken, Vor/Zurück in der Reihenfolge aus `docs.yaml`, Link „Auf GitHub bearbeiten“ auf
  die Quelldatei der aktuellen Sprache. Material-3-Stil wie die übrige Seite.

#### Screenshots
- WebUI-Bilder aus `dev/screenshots.py` (helle Variante) in `docs/img/<en|de>/<name>.webp`, damit sie auch auf
  GitHub angezeigt werden. Neue Motive für Anleitungsschritte: Gerät koppeln (Kopplungscode), Kind anlegen und
  zuordnen, Programm-Whitelist, Webfilter, Spiele. Client-Bilder (Linux/Windows) von Hand aus der VM, nur wo sie
  helfen.

#### Verweise
- **Startseite:** Schnellstart bleibt; Button „Vollständige Anleitung“ und neuer Menüpunkt „Anleitungen“/„Docs“
  führen ins Wiki (`params.docs` auf die Wiki-URL umstellen).
- **README (DE/EN):** Abschnitt Installation verlinkt ins Wiki; Hinweis „Anleitungen: <Wiki> (Quelle: `docs/`)“.
- **Release-Notes** (`kidctl-release.sh`), **WebUI** (`repo.html`): Links ins Wiki (EN; WebUI in Sprache des
  Eltern-Accounts, falls einfach möglich).
- **`server/app/config.py`:** Fehlermeldung nennt Wiki-URL und `docs/postgresql-migration.md`.
- Wiki-URLs auf `adriannoss.github.io/kidCTL/...` bleiben beim späteren Domain-Wechsel gültig (GitHub leitet
  weiter).

## Sicherheitsrichtlinie (entschieden 2026-11-17 per /grill-me, umgesetzt 2026-11-17; GitHub-Einstellungen von Hand)

Ziel: vertraulicher, klarer Meldeweg für Sicherheitslücken statt öffentlicher Issues; GitHub-Hinweis
„no SECURITY.md“ beheben.

### Dateien
- `SECURITY.md` (EN) und `SECURITY.de.md` (DE) im Repo-Root, gleicher Inhalt, im Paritäts-Check. GitHub wertet
  `SECURITY.md` aus (Security-Reiter). Zusätzlich per Einzeldatei-Mount als Wiki-Seite `security` (Gruppe
  Sicherheit).
- Abschnitt „Reporting“ in `docs/code-signing*.md` wird zum Verweis auf `SECURITY.md` (der bisherige Satz
  „open an issue“ entfällt). README (DE/EN) bekommt einen kurzen Abschnitt „Sicherheit“ mit Link.

### Inhalt `SECURITY.md`
- **Meldeweg:** bevorzugt GitHub Private Vulnerability Reporting (Security → „Report a vulnerability“);
  alternativ `security@kidctl.de`. Kein PGP. Ausdrücklich: Sicherheitslücken
  nie als öffentliches Issue, Pull Request oder in Foren melden.
- **Unterstützte Versionen:** nur die jeweils neueste Version (vor 1.0); Fixes erscheinen als neue Patch-Version,
  keine Backports. Server und Agenten auf demselben Stand halten.
- **Im Rahmen (vertraulich melden):**
  - Rechteausweitung, Codeausführung, Umgehen von Anmeldung/TOTP/Sitzungen, CSRF/XSS/Injection im WebUI,
    Angriffe auf die Verbindung Agent ↔ Server (Token, Kopplung, Update-Signaturen), Datenabfluss; im
    SaaS-Betrieb Zugriff auf Daten anderer Familien.
  - Neue Wege, wie ein Kind **ohne Admin-Rechte** Zeitlimit, Programm- oder Website-Filter umgeht oder die
    Durchsetzung abschaltet.
- **Nicht im Rahmen:** physischer Zugriff ohne die Härtung aus `docs/hardening.md`; VPN bzw. feste IP-Adressen;
  alle in `docs/limitations.md` dokumentierten Restlücken; Angriffe, die Admin-/Root-Rechte auf dem Kinderrechner
  voraussetzen; fehlende Best-Practice-Header ohne konkrete Auswirkung.
- **Ablauf und Fristen:** Eingangsbestätigung innerhalb von 7 Tagen, erste Einschätzung (bestätigt, Schwere)
  innerhalb von 14 Tagen, Fix so schnell wie möglich – Ziel bei schweren Lücken 30 Tage. Coordinated Disclosure:
  Veröffentlichung nach dem Fix-Release, spätestens nach 90 Tagen in Absprache mit dem Melder.
- **Veröffentlichung:** GitHub Security Advisory je bestätigter Lücke (betroffene/behobene Version, CVSS,
  Beschreibung ohne fertige Ausnutzungsanleitung, Dank an den Melder, wenn gewünscht); CVE über GitHub ab
  mittlerer Schwere. News und Release-Text verlinken das Advisory. **Kein Bug-Bounty.**
- **Testregeln:** nur an eigener Installation bzw. Test-VM, nie an `app.kidctl.de` oder anderen fremden
  Instanzen; nur eigene Kinderrechner; kein DoS, kein Social Engineering, kein Zugriff auf fremde Daten (passiert
  es doch: sofort aufhören, nichts speichern, melden).
- **Safe Harbor:** Wer in gutem Glauben und nach diesen Regeln forscht und meldet, gegen den wird nicht rechtlich
  vorgegangen. Formulierung bei der Anwaltsprüfung (AGB/Datenschutz/Impressum) mit prüfen lassen.

### Restlücken als öffentliche Doku
- Der Abschnitt „Bewusst nicht umgesetzt / Restlücken“ zieht nach `docs/limitations.md` / `limitations.de.md`
  (Wiki-Gruppe Sicherheit) und ist dort die **einzige Quelle**, für Eltern verständlich formuliert. Interne
  Begründungen bleiben in den Fachabschnitten der Spec. In der Spec bleibt nur ein Verweis.
- README-Kurzliste „Limitations“ und `SECURITY.md` verlinken darauf.

### `security.txt` (RFC 9116)
- **Projektseite:** `website/static/.well-known/security.txt`; `Expires` setzt der Pages-Build auf heute + 1 Jahr.
  Wirksam seit der eigenen Domain (`https://kidctl.de/.well-known/security.txt`).
- **kidCTL-Server:** Route `/.well-known/security.txt` (ohne Login), `Expires` dynamisch (+ 1 Jahr),
  `Contact: mailto:security@kidctl.de` und GitHub-Meldeweg, `Policy:` auf `SECURITY.md`, `Preferred-Languages:
  en, de`. Optionale Umgebungsvariable `SECURITY_CONTACT`: zusätzlicher Kontakt des Betreibers (zuerst gelistet).

### GitHub-Funktionen
- Dependabot-Alerts; Dependabot-Updates wöchentlich, gruppiert (ein PR je Ökosystem) für `pip`
  (`server/requirements.txt`) und `github-actions` (`.github/dependabot.yml`).
- Secret Scanning mit Push Protection.
- CodeQL im Standard-Setup (Python, GitHub Actions).
- Workflow `.github/workflows/audit.yml`: wöchentlich (und von Hand) `pip-audit` gegen die Pakete aus
  `packaging/windows/vendor.lock` (Dependabot kennt das Format nicht); Fund → Workflow schlägt fehl.

## Geplante Funktionen (entschieden 2026-11-21, Planungssitzung; noch nicht umgesetzt; Zielversionen am 2026-11-22 um eins verschoben, weil 0.46 Update-Knopf und Info-Seite bringt)

Priorisiert mit Adrian anhand einer Funktionsrecherche (verbreitete Kindersicherungen, Wünsche von Eltern und
Self-Hostern). Plan mit Etappen: `dev/planung/PLAN-2026-11.md`. Fachliche Details je Funktion vor der Umsetzung per
/grill-me klären und hier ergänzen.

**Etappe 1 – Alltag (Ziel 0.47):**
- ~~**Freigabe anfragen**~~ – bereits seit 0.24.0 umgesetzt (siehe „Freigabe-Anfragen“ unter Chat/Anfragen,
  Changelog 2026-10-20); bei der Planung übersehen, am 2026-11-23 mit Adrian als erledigt abgeschlossen.
- **Ferien und Feiertage:** Ferienzeiträume je Familie mit eigener Ferienvorlage je Kind; gesetzliche Feiertage je
  Bundesland/Land gelten wie ein wählbarer Wochentag (z. B. „wie Sonntag“). Feiertagsdaten lokal im Server, kein
  externer Abruf. Details: „Bildschirmzeit → Ferien und Feiertage“ (in Arbeit seit 2026-11-23).
- **Mehrere Zeitfenster je Tag:** umgesetzt als Rahmen + bis zu 3 Sperrzeiten je Tag und Fenster über Mitternacht,
  siehe „Bildschirmzeit → Sperrzeiten und Fenster über Mitternacht“ (entschieden 2026-11-23).
- Parallel: **Lutris und Bottles** als Spielequellen (Roadmap `wine-launchers`).

**Etappe 2 – Zeit-Logik (Ziel 0.48):**
- **Limit je Programm/Kategorie:** Verallgemeinerung des Spiele-Limits (eigenes Tageskontingent für ein Programm
  oder eine Kategorie, z. B. Browser) – umgesetzt, siehe „Limit je Programm/Kategorie“.
- **„Zählt nicht“-Programme:** Programme (z. B. Lern-Apps, Office) laufen im Zeitfenster, ohne das Kontingent zu
  verbrauchen. Browser, Terminals und Launcher sind nie ausnehmbar (Umgehung). Umgesetzt, siehe Abschnitt
  „„Zählt nicht“-Programme“.
- **Zeitkonto:** nicht genutzte Minuten teilweise auf Folgetage übertragen, mit Deckel je Tag und Verfall; offline
  mit gecachter Policy.

**Etappe 3 – Belohnung, Benachrichtigung, Desktops (Ziel 0.49):**
- **Aufgaben → Zeit verdienen:** Eltern legen Aufgaben mit Minutenwert an, das Kind meldet „erledigt“ in der
  Tray-App, Eltern bestätigen → Gutschrift (bestehende Tagesausnahme).
- **Weich-Modus:** je Kind wählbar – bei Zeitende nur warnen und Eltern informieren statt sperren (für Ältere).
- **Weitere Alarmkanäle:** ntfy, generischer Webhook, Matrix für Push-Alarme und Anfragen; nur Hinweis + Link,
  kein Inhalt; Kanäle tragen Eltern selbst ein.
- **Cinnamon und COSMIC** (Roadmap `desktops`).

**Ideen ohne Termin (nicht auf der Roadmap):** Lernmodus auf Knopfdruck, lokale API für Home Assistant, eigene
Wochenstatistik für das Kind in der Tray-App, Clients für macOS/Android (bewusst nicht jetzt).

## „Zählt nicht“-Programme (entschieden 2026-11-25, umgesetzt 2026-11-25)

Abgestimmt mit Adrian (über die Planungssitzung, alle Empfehlungen übernommen):

- **Semantik:** Ein Zähltakt verbraucht keine Bildschirmzeit, wenn in der Sitzung mindestens ein „zählt nicht“-
  Programm läuft und sonst **kein anderes Katalog-Programm, kein Spiel und kein Browser** (auch ohne Katalog-Eintrag,
  erkannt am Programmnamen). Leerer Desktop zählt wie bisher. Kein Fokus-Fenster (Agent sieht es unter Wayland bzw.
  als Dienst nicht zuverlässig). KidCTLs eigene Tray-App ist neutral. Gezählt wird anhand des vorigen Takts (≤ 5 s
  Versatz).
- **Zeitende:** unverändert Vorwarnung + Abmeldung; „zählt nicht“ öffnet nach Zeitende nichts (das wäre K9
  Lernmodus). Zeitfenster und Sperrzeiten gelten weiter. Spielzeit unberührt.
- **Spiele** (Kategorie, markiert, erkannt) sind nicht ausnehmbar; „Spiel“ und „Zählt nicht“ schließen sich aus
  (Server verwirft, WebUI blendet aus).
- **Nie ausnehmbar** (`kidctl_common/freeapps.py`, Server und Agent): Kategorien WebBrowser, TerminalEmulator, Shell,
  FileManager, Development, PackageManager; Programmnamen von Browsern, Terminals/Shells, Interpretern, Dateimanagern
  und Spiele-Launchern (Linux und Windows), Steam, bekannte Flatpak-IDs, Store-Apps (Edge, Terminal, Xbox).
- **Umgehungsschutz im Agenten:** Linux – Programmdatei muss root gehören, Name nicht auf der Liste (auch nicht im
  Ordner der App), Flatpak nur mit eigenem Mount-Namensraum (Cgroup-Name allein ist fälschbar), kein Container, kein
  Electron mit Optionen, die fremden Code laden. Windows – nur Store-Apps und Programme unter Programme/Windows
  (Benutzerordner wäre austauschbar), Name und `OriginalFilename` der Versionsinfo nicht auf der Liste (umbenannte
  Browser-Kopie).
- **Browser im Hintergrund:** Installer bzw. Paket setzen Edge `StartupBoostEnabled=0`/`BackgroundModeEnabled=0`,
  Chrome/Chromium `BackgroundModeEnabled=false` – sonst liefe Edge unsichtbar weiter und verhinderte „zählt nicht“.
- **Protokoll:** optionales Feld `ChildPolicy.free_apps` (keine neue Protokollversion; ältere Agenten ignorieren es
  und zählen alles). Agenten ab 0.48 melden die Fähigkeit `free_apps` im `hello`; der Programme-Tab nennt Geräte ohne
  sie. Tray: `TrayState.free` → Zeile „Die Zeit läuft gerade nicht“.
- **Daten:** Tabelle `child_app_rule(child_id, key, free)` (Alembic `0029`), allgemein gehalten für weitere Regeln je
  App (K4 Limit je Programm). Programmzeit-Statistik zählt ausgenommene Programme weiter.
- **Restlücken:** siehe `docs/limitations*` („Zählt nicht“ nach geöffneten Programmen).

## Limit je Programm/Kategorie (entschieden 2026-11-25, umgesetzt 2026-11-25)

Abgestimmt mit Adrian (über die Planungssitzung, alle Empfehlungen übernommen):

- **Ziele:** einzelne Programme aus dem Katalog des Kindes (auch eigene Programme; Steam auf jeder Plattform) und feste
  Kategorien aus `kidctl_common/applimits.py`: *Browser* (freedesktop `WebBrowser`, zusätzlich am Programmnamen wie
  `freeapps.BROWSERS`, auch ohne Katalog-Eintrag), *Chat* (`InstantMessaging`, `Chat`), *Musik & Video*
  (`AudioVideo`, `Audio`, `Video`). Windows nutzt die geschätzten Katalog-Kategorien. Keine frei wählbaren Kategorien.
- **Wert:** ein Minutenwert (0–1440) für alle Tage, nicht je Wochentag oder Ferienvorlage; höchstens 20 Limits je Kind.
- **Zählung:** nur in gezählter Sitzung, solange mindestens ein passender Prozess läuft (kein Fokus); parallele
  Programme derselben Regel zählen einmal je Takt. Fällt ein Programm unter mehrere Regeln (Firefox + *Browser*), zählt
  es für jede. Auch „Zählt nicht“-Programme können ein Limit haben (Zeit zählt fürs Limit, nicht für die
  Bildschirmzeit). KidCTLs eigene Tray-App zählt nie.
- **Geräteübergreifend** wie die Spielzeit: `usage_report.entries[].limit_seconds` (je Limit, absolut, Maximum-Regel),
  `usage_update.others_limit_seconds`; offline zählt jedes Gerät lokal weiter (gespeichert in `usage.json`).
- **Ablauf:** wie beim Spiele-Limit (`gamelimit.AppLimit`): Vorwarnung 5 und 1 min vorher (nur bei laufendem
  Programm), danach passende Prozesse schließen (SIGTERM, nach 10 s SIGKILL bzw. Windows hart), neu gestartete bis
  Tagesende ebenso. Kein Anfrageknopf, die Meldung verweist auf den Chat. Rest des Rechners bleibt nutzbar.
- **Spiele-Limit** bleibt unverändert daneben (nicht migriert).
- **Gutschriften:** Zeitkonto, Bildschirmzeit-Gutschriften und genehmigte Anfragen wirken nicht auf Programm-Limits;
  keine eigene Gutschrift je Limit in 0.48 (Eltern ändern das Limit).
- **Protokoll:** optionale Felder `ChildPolicy.app_limits` (`key`, `minutes`, `name` – bei Kategorien leer, der Agent
  übersetzt selbst), `limit_seconds`, `others_limit_seconds`, `TrayState.limits`; keine neue Protokollversion.
  Agenten ab 0.48 melden die Fähigkeit `app_limits`; die Karte nennt Geräte ohne sie.
- **Daten:** `child_app_rule.limit_minutes` (Schlüssel auch `cat:…`), `usage_record.limit_seconds` (JSON,
  verschlüsselt), Alembic `0031`. Zeilen ohne „zählt nicht“ und ohne Limit werden gelöscht.
- **Anzeige:** Karte „Limits für Programme“ im Tab *Zeiten* (hinzufügen, Minuten ändern, entfernen – ohne
  JavaScript), Zeile je Limit „verbraucht von Limit“ im Tab *Heute*, Tray-Zeile „<Name>: noch … von …“ bzw. „für heute
  aufgebraucht“ (Linux und Windows, kein eigenes Vordergrundfenster).
- **Restlücken:** siehe `docs/limitations*` („Limits für Programme nach Name und Kategorie“).

## Nintendo-Switch-Spielzeit (experimentell, Phase 1 nur lesen; entschieden 2026-11-26, umgesetzt 2026-11-26)

Ursprung: Feature-Wunsch eines Nutzers, Arbeitspaket von Adrian. Recherche: `dev/planung/2026-11-26-nintendo-recherche.md`.
Abgestimmt mit Adrian am 2026-11-26, Empfehlungen übernommen außer der Zuordnung.

- **Quelle:** inoffizielle Cloud-API der App „Nintendo Switch Parental Controls“ über `pynintendoparental==2.7.0`
  (`pynintendoauth==1.0.3`, MIT), wie die HA-Integration `nintendo_parental_controls`. In UI und Doku „experimentell“.
- **Nur Anzeige:** Die Switch-Zeit zählt nicht ins Kontingent, ins Zeitkonto oder in die Bildschirmzeit der
  Statistik. Kein Einfluss auf Policy und Agent. Schreiben (Limits, Bonuszeit, Sperre) ist Phase 2.
- **Zuordnung je Spielerprofil** (nicht je Konsole): Ein Profil gehört höchstens einem Kind. Nicht zugeordnete Profile
  bleiben gespeichert und werden einmalig geloggt (ohne Namen). Limit, Restzeit und Stand gelten je Konsole und stehen
  beim Kind als „Konsole gesamt“.
- **Schalter global in `.env`:** `KIDCTL_NINTENDO` (Standard an bei `single`, aus bei `saas`, bis Nintendos
  Rate-Limits bei vielen Familien bekannt sind), `KIDCTL_NINTENDO_INTERVAL` (Standard und Minimum 60 s). Je Familie
  in der DB nur Verknüpfung und Zuordnung.
- **Einrichtung** unter Einstellungen → „Nintendo Switch“ (alle Eltern, ohne JavaScript): Login-Link erzeugen, den
  kopierten `npf54789befb391a838://auth#…`-Link einfügen. Der PKCE-Verifier liegt verschlüsselt in der DB (30 min). „Neuen Link erzeugen“
  ersetzt einen offenen (z. B. schon verbrauchten) Link samt Verifier.
  Die Bibliothek hält ihn nur im Objekt, daher wird `_auth_code_verifier` gesetzt. Hinweis: Gerät ohne Nintendo-App
  nutzen.
- **Session-Token:** `Sealed` (Familienschlüssel), nie im Log (Logger der Bibliothek fest auf WARNING, weil DEBUG
  Spielnamen und Nicknames schreibt), nicht im Familien-Export (`nintendo_link` ausgeschlossen). „Verbindung
  trennen“ löscht Token, Konsolen und Profile. Die Spielzeit je Kind bleibt.
- **Abruf** (`app.nintendo.run_forever`, ein Client je Familie im Speicher): `discover_devices()`, dann
  `Device.update(monthly_summary=False)` je Konsole, damit Fehler je Konsole ankommen. Restzeit bei „kein Limit“
  bzw. unbegrenzter Bonuszeit = leer; die Bibliothek liefert dann die Minuten bis Mitternacht.
- **Fehler:** ungültiges Token → Status „reauth“, kein weiterer Abruf. Andere Fehler → Status „error“ mit Code,
  Konsolen „veraltet“, letzte Werte bleiben, Backoff Intervall × 2ⁿ bis 15 min. Fehler einer einzelnen Konsole
  markieren nur diese. Fehlende Felder neuer Konsolen werden toleriert.
- **Anzeige:** Karte „Nintendo Switch“ im Tab *Heute* je zugeordnetem Profil (heute gespielt, Konsole gesamt/Limit,
  Restzeit, Stand, Spiele heute); eigene Karte in der *Statistik* (Minuten je Tag, 14 Tage), getrennt von der
  Bildschirmzeit.
- **Daten:** `nintendo_link`, `nintendo_console`, `nintendo_player`, `nintendo_usage` (Tag je Profil, Kind,
  Minuten, Spiele als verschlüsseltes JSON), Alembic `0032`. Aufbewahrung `NINTENDO` 395 Tage wie `USAGE`.
- **Doku:** `docs/nintendo*.md` (Wiki, Gruppe Betrieb). Smoke-Test mit echtem Konto: `dev/nintendo_smoke.py`
  (Token aus `NINTENDO_SESSION_TOKEN`).

## Feature vorschlagen (entschieden 2026-11-26, umgesetzt 2026-11-26)

Teil B des Arbeitspakets „Nintendo-Anbindung + Feature-Request-Kanal“; abgestimmt mit Adrian (alle Empfehlungen
übernommen):

- **Schalter:** `FEATURE_REQUEST_EMAIL` in `.env`, Standard leer = aus (Seite 404, keine Links); braucht SMTP.
  app.kidctl.de trägt `features@kidctl.de` ein, Self-Hoster nur, wenn sie wollen – ohne Eintrag verlässt nichts die
  Instanz.
- **Ort:** eigene Seite `/feature-request`, verlinkt als Karte in *Einstellungen* und auf der Seite *Support* (SaaS);
  kein eigener Navigationspunkt. Nur angemeldete Eltern, CSRF, in der Support-Ansicht gesperrt.
- **Formular:** Titel (Pflicht, ≤ 120), Kategorie (Pflicht: Neue Plattform/Gerät, Bedienung, Regeln/Limits,
  Benachrichtigungen, Sonstiges), Beschreibung (Pflicht, ≤ 5000, Plaintext), Wichtigkeit (optional:
  niedrig/mittel/hoch), „Rückfragen erlaubt“ (Standard aus; nur mit bestätigter Mailadresse). Serverseitig:
  HTML-Tags und Steuerzeichen entfernt, Titel einzeilig, Längen nach dem Bereinigen. Danach Bestätigung „Danke, dein
  Vorschlag ist angekommen“.
- **Mail:** Template `mail/feature_request.html` (HTML zum Lesen, mit Inhalt – bewusste Ausnahme von „nur Hinweis +
  Link“, weil sie an den Betreiber geht). Betreff `[KidCTL Feature] <Titel>` ohne Markennamen davor. Die Textfassung
  ist exakt das Format `KIDCTL-FEATURE-REQUEST v1` ohne Fußzeile (Flag `plain_text` in `mail/base.txt`) – Schnittstelle
  zum Postfach-Agenten, nur nach Rücksprache ändern. Kategorie und Wichtigkeit stehen immer deutsch darin, `Datum` in
  UTC (ISO 8601), `ID` als UUID4, `Kontakt` nur mit Häkchen (dann auch `Reply-To`), sonst `-`.
- **Rate-Limit:** 5 Vorschläge je Elternteil in 24 Stunden, gezählt in `login_attempt` (`kind="feature"`, Löschung
  nach der Frist der Anmeldeversuche); keine Migration, kein Inhalt in der Datenbank, kein Verwaltungsprotokoll (keine
  Betreiber-Aktion). Fehlgeschlagener Versand zählt nicht.

## Info und Neuigkeiten (entschieden 2026-11-22 per /grill-me, umgesetzt 2026-11-22)

- **Server-Version:** `server/app/release_notes.py`, oberster Eintrag = laufende Version (Version, Datum, 2–4
  Stichpunkte DE/EN für Eltern). Bei jedem Release mitpflegen; ein Test prüft die Gleichheit mit
  `agent/kidctl_agent/__init__.py`.
- **Info-Abschnitt:** Eltern unter Einstellungen (`/settings#info`), Betreiber unter `/admin/info` (beide Rollen;
  Navigation, am Handy unter „Mehr“). Gleicher Baustein `_info.html`: Version, Release-Datum, Protokollversion,
  Stichpunkte der laufenden Version, Lizenz AGPL-3.0, security@kidctl.de, Links Projektseite, Release Notes, Doku,
  Quellcode (`SOURCE_URL`). Whitelabel: zusätzlich „<Name> basiert auf KidCTL“.
- **Neuere Version:** Der Server ruft einmal täglich `https://kidctl.de/version.json` ab (ohne Kennungen, Ergebnis nur
  im Speicher); abschaltbar mit `UPDATE_CHECK=0`. Die Projektseite erzeugt die Datei beim Build aus den
  GitHub-Releases (neuester Release ohne Draft/Prerelease). Hinweis nur im Info-Abschnitt.
- **Hinweis nach Update:** Je Elternteil `parent.seen_version` (neue Konten: laufende Version; Migration: 0.45.2).
  Ist der Server neuer, zeigt die Übersicht einmalig eine Karte „Neu in Version …“ mit den Stichpunkten aller
  Versionen seitdem, Link auf die Release Notes und „Verstanden“ (`POST /info/seen`, CSRF, ohne JavaScript).
- **Tray (Linux und Windows):** Menüpunkt „Info“ öffnet einen kleinen Dialog mit der installierten Version (aus dem
  Agent-Paket, Agent und Tray kommen aus demselben Paket) und Links zu Projektseite, Quellcode und Lizenz; kein
  Sicherheitskontakt. Texte gemeinsam, Windows-Optik über `winstyle`.

## Feature-Inbox (entschieden 2026-11-26 mit Adrian, Arbeitspaket Teil C; umgesetzt 2026-11-26)

Nur für den Betreiber von kidctl.de, nicht Teil des Self-Hosting. Details: `tools/feature-inbox/README.md`.

- **Dienst:** `tools/feature-inbox/` als eigener Container im Stack (`docker-compose.yml`, Profil `feature-inbox`,
  nur ausgehendes Netz, ohne Traefik und Datenbank). Liest `features@kidctl.de` per IMAP (TLS) alle 15 min,
  parst `KIDCTL-FEATURE-REQUEST v1` (Format aus Teil B), Fremdmails → Ordner `Unsortiert`, verarbeitete →
  `Verarbeitet`. Idempotent über die Request-ID (lokaler Zustand + Marker im Issue). Probelauf ohne `--apply`.
- **Aufbereitung:** Claude-API, Modell `FEATURE_INBOX_MODEL` (Standard `claude-haiku-5-5`), `FEATURE_INBOX_LLM=none`
  schaltet ab; weitere Backends (lokales Modell) in `llm.py`. Zusammenfassung, Kategorie, Aufwand S/M/L, Bereiche,
  Duplikate gegen offene `feature-request`-Issues, Rückfragen nur bei erlaubtem Kontakt.
- **Issues:** Labels fest `feature-request` + `needs-triage`; Originaltext als entschärfter Zitatblock.
  Kontaktadresse nie im (öffentlichen) Issue, nur im lokalen Zustand; Logs maskiert.
- **Prompt-Injection:** Mailtext ist Daten; Modell ohne Tools, Antwort per Structured Output, Code übernimmt nur
  geprüfte Felder; GitHub-Aufrufe nur „Issues auflisten“ und „Issue anlegen“. Token: Fine-grained, nur Repo,
  nur Issues. Geheimnisse in eigener `feature-inbox.env` (Ausnahme von „nur `.env`“, damit Server und Inbox
  getrennt bleiben).
- **Sperre der Issue-Automatik** (lokaler Zeitplan-Task `kidctl-issues-taeglich`): Issue-Liste nur über
  `dev/issues_freigegeben.py`; gesperrt sind `needs-triage` und `feature-request` ohne `approved`. Freigabe durch
  Adrian: `needs-triage` entfernen, `approved` setzen.
- **Tagesübersicht:** Mail per SMTP an `FEATURE_INBOX_REPORT_TO` ab `FEATURE_INBOX_REPORT_HOUR` (Standard 7 Uhr):
  neue Issues, Duplikat-Verdacht, Anzahl Unsortiert, Fehler.

## USB-Datenträger (Grundsatz entschieden 2026-11-26; Details offen, noch nicht umgesetzt)

Recherche: `dev/planung/2026-11-26-usb-datentraeger-recherche.md` (Mechanismen, Restlücken, offene Fragen).

- Alle USB-Massenspeicher (Sticks und Festplatten) sind für zugeordnete Kinder **gesperrt**, auf Linux und Windows.
  Zugreifbar sind nur Geräte, die Eltern erlaubt haben.
- Erkennung erlaubter Geräte über die **Hardware-Kennung** (USB-Seriennummer), nicht über UUID/Label. Der Agent meldet
  gesehene Datenträger, Eltern erlauben sie im WebUI, die Policy enthält die Kennungen (offline wirksam). Geräte ohne
  eindeutige Seriennummer werden als „nicht eindeutig“ gekennzeichnet.
- Gesperrt wird der Zugriff (Einbinden bzw. Lesen), nicht nur das Ausführen. Linux: polkit-Regel für udisks2 mit
  Ausnahme je Seriennummer. Windows: Agent verhindert den Zugriff auf fremde Laufwerke in der Sitzung des Kindes.
- Nie betroffen: Tastatur, Maus, Headset, Controller (HID), Admins und nicht zugeordnete Benutzer.
- Bereits umgesetzt (0.48): Programme auf Wechseldatenträgern sind nicht freigebbar (Programmkontrolle
  „Wechseldatenträger“).
- Erlaubte Geräte: nur **Dateien** zugreifbar; Programme darauf bleiben gesperrt und nicht freigebbar (wie 0.48).
  Launcher-Bibliotheken (Steam) und Store-Apps auf erlaubten Geräten laufen über den freigegebenen Launcher weiter.
- Liste erlaubter Geräte **je Familie** (gilt für alle Kinder).
- **Schalter je Kind**: neue Kinder gesperrt, bestehende Kinder bleiben nach dem Update erlaubt, bis Eltern einschalten.
- Mitgesperrt: Abbilder (ISO, `loop-setup`), CD/DVD, SD-Karten (Bus SD/MMC) und Handys (MTP/WPD).
- Offen: Zielversion.

## Bewusst nicht umgesetzt / Restlücken

Einzige Quelle für die Restlücken ist die öffentliche Doku **`docs/limitations.de.md`** (Englisch:
`docs/limitations.md`, im Wiki Gruppe Sicherheit), für Eltern verständlich nach Überall/Linux/Windows gegliedert.
Neue Restlücke → dort im selben Commit eintragen (siehe CLAUDE.md „Sicherheit“). Die technischen Hintergründe stehen
in den Fachabschnitten: Linux in „Programm-Whitelist“ und „Website-Filter“ (jeweils „Restlücken – Hintergrund“),
Windows in „Architektur → Windows“ („Restlücken – Hintergrund (Windows)“).

## Offene Punkte

- Subdomain `app.kidctl.de` (bis 2026-11-21 `kids.nossco.de`, alte Adresse leitet weiter).

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
- 2026-10-16: Programmliste übersichtlicher (Nutzer-Rückmeldung „lang und unübersichtlich“): Agent meldet
  `Categories` der .desktop-Einträge (`AppEntry.categories`), Server speichert sie (`device_app.categories`,
  Migration) und gruppiert; Suchfeld + „Alle/Keine“ (`static/programs.js`). Version 0.13.0. Getestet:
  Server 69 (Gruppierung inkl. Vorrang, Speicherung, eingeklappte Systemgruppe, Migration), Agent 127
  (Kategorien aus .desktop); Screenshot mit 89 Apps des Entwicklungsrechners.
- 2026-10-16: Steam startete bei Programmkontrolle nicht (Nutzer-Test: `~/.local/share/Steam` war ein echter
  Ordner, angelegt während die Kontrolle aus war → bei noexec-Home nicht ausführbar). Recherche der
  Startkette (bin_steam.sh über `env bash` → steam.sh → ubuntu12_32/steam → reaper/steam-launch-wrapper →
  SteamLinuxRuntime _v2-entry-point/pressure-vessel/bwrap → Proton/Spiel): durch die Abstammungsregel
  bereits abgedeckt. Fix: Verweis für alle zugeordneten Kinder, automatischer Umzug nach Abmeldung,
  Integritätsmeldung. Version 0.14.0. Getestet: Agent 131 (Umzug nach Abmeldung inkl. Verweis/Rechte,
  Symlink-Angriff auf Pfadteil → nichts bewegt, andere Partition → Warnung ohne Datenverlust,
  Integritätsmeldung).
- 2026-10-18: Shells wählbar (Nutzerwunsch: fish fehlte in der Programmliste): `apps.scan_shells`
  aus `/etc/shells`, neue Gruppe „Terminal & Shells“, Hinweis im WebUI, Shells ohne Programmzeit.
  Version 0.15.0. Getestet: Agent 133 (Shell-Erkennung inkl. sh/rbash→bash, git-shell ignoriert),
  Server 70 (Gruppe für Shell und Konsole).
- 2026-10-18: Sperrprotokoll: Ohne Steam-Freigabe werden Programme der Steam-Bibliothek und Hilfsprogramme
  des Steam-Starters (z. B. `cmp`, `srt-logger`) als ein Eintrag „Steam – nicht freigegeben – <programm>“
  mit Schlüssel `exe:/usr/bin/steam` protokolliert statt als einzelne, irreführende Programme.
  Getestet: Agent 134 (cmp/srt-logger mit Steam-Abstammung → „Steam“, ohne Abstammung unverändert).
- 2026-10-18: Desktop-Verknüpfungen erlaubter Programme vertraut (Nutzer-Test: Steam-Spiel-Verknüpfung auf
  dem Desktop startete nur ohne Programmkontrolle). Ursache per Test im User-Namespace bestätigt:
  `QFileInfo.isExecutable()` = false auf noexec trotz 0755. Neues Modul `desktoptrust`. Version 0.16.0.
  Getestet: Agent 140 (nur erlaubte Einträge, XDG-Desktop-Ordner, Flatpak-ID, Symlink/harter Link/
  fremder Besitzer/Pfad außerhalb des Homes abgelehnt, Systemskript-Regel). Nicht in echter
  KDE-Sitzung getestet.
- 2026-10-18: Sichttest auf dem Testrechner (0.16.0) vom Nutzer bestätigt: Steam-Desktop-Verknüpfung startet bei aktiver Programmkontrolle, Steam-Spiel startet.
- 2026-10-18: Heroic (AUR `heroic-games-launcher`, System-Electron) unter Programmkontrolle (Paket
  0.17.0). Anlass: Nutzer-Test, Heroic startete nicht („electron gesperrt“). Plan vom Nutzer
  bestätigt. Paket-Ordner-Regel, Electron-Regel, Spiele-Launcher-Regel, Heroic-Ordner unter
  `/srv/heroic/<user>` (Spiele, Wine/Proton, umu) mit Umzug, gemerkte Abstammung für verwaiste Prozesse, Programmzeit für
  Electron-Apps (über die App). Steam-Bibliothekscode verallgemeinert (`ensure_libraries`,
  Integrität `steam:`/`heroic:`). Getestet: Agent 150 (Paket-Ordner aus `pacman -Ql`-Ausschnitt,
  Electron erlaubt/fremde App/`-r`/`--inspect`/`ELECTRON_RUN_AS_NODE`/`NODE_OPTIONS`, Hilfsprozesse nur
  mit erlaubtem Hauptprozess, Launcher-Nachfahren nur bei Freigabe, Proton nur mit Steam,
  verwaister wineserver nach Policy-Änderung, Heroic-Verweise anlegen/umziehen/Symlink-Angriff,
  Programmzeit), Server 70. Paket-Ordner gegen das echte `pacman -Ql` des Entwicklungsrechners
  geprüft (`/usr/bin/heroic` → `/usr/lib/heroic-games-launcher`). **Nicht getestet:** echtes Heroic mit
  GOG-Spiel auf dem Testrechner.
- 2026-10-18: Fix Heroic/Electron (Paket 0.17.1). Nutzer-Test 0.17.0: Electron weiter gesperrt.
  Ursache: Chromium überschreibt `/proc/<pid>/cmdline` gleich beim Start mit der Befehlszeile als
  einem String (setproctitle) – App-Pfad und `--type=` wurden nicht erkannt. Electron-Argumente
  werden in diesem Fall an Leerzeichen getrennt. Getestet: Agent 151; zusätzlich echte electron43-
  Test-App auf dem Entwicklungsrechner aus `/proc` klassifiziert – mit Freigabe Hauptprozess,
  Zygote, GPU-, Netzwerk- und Renderer-Prozess erlaubt, ohne Freigabe alle gesperrt.
- 2026-10-18: Heroic: GOG-/Epic-Anbindung (Paket 0.17.2). Nutzer-Test 0.17.1: Heroic startet, aber
  `legendary` scheitert („failed to map segment from shared object“) und GOG-Anmeldung schlägt fehl.
  Ursache: Die Python-Zipapps gogdl/legendary entpacken native Module nach `~/.cache/heroic_gogdl`
  bzw. `~/.cache/legendary` und laden sie – im noexec-Home unmöglich. Beide Ordner jetzt als Verweise
  in `/srv/heroic/<user>/cache/`. Umzug öffnet auch Zwischenordner des Ziels ohne Symlink-Verfolgung.
  Getestet: Agent 152 (Verweise, Umzug, Symlink-Angriff auf den Zwischenordner des Ziels); gogdl 1.3.0
  und legendary 0.21.1 laufen auf dem Entwicklungsrechner mit ausführbarem Cache-Ordner.
- 2026-10-18: Spieleordner ohne Launcher-Freigabe `noexec` (Paket 0.18.0), Variante vom Nutzer an mich
  delegiert. Dabei gefunden: Der automatische Umzug vorhandener Ordner aus dem Home scheitert bei
  aktiver Programmkontrolle immer (`rename` über die Grenze des noexec-Bind-Mounts → `EXDEV`, im
  Namespace nachgestellt), ebenso bei btrfs-Subvolumes (CachyOS `@home`). Meldung und Anleitung
  korrigiert; die Caches von gogdl/legendary werden stattdessen als Kind gelöscht und verlinkt
  (sofort, ohne Abmeldung). Getestet: Agent 154 (Ein-/Aushängen, Symlink abgelehnt, Zuordnung
  Freigabe → gesperrte Ordner, Cache-Ersetzung als Kind). Nicht auf dem Testrechner getestet.
- 2026-10-18: Interpreter-Einschleusung gesperrt (Paket 0.19.0). Lücke nachgewiesen: eine `.pth`-Datei
  bzw. `usercustomize.py` im User-Site-Ordner läuft in jedem erlaubten System-Python-Skript (mit
  `/usr/bin/pip` gezeigt). Regel siehe Programm-Whitelist. Getestet: Agent 157 (sauberes Skript
  erlaubt; `PYTHONPATH` Home/leer/relativ, `PYTHONUSERBASE`, `PYTHONSTARTUP`, `PYTHONINSPECT`,
  `PERL5OPT`, Lua-Pfade; User-Site leer erlaubt, mit `.pth` gesperrt). Entwicklungsrechner: keine dieser
  Variablen in der KDE-Sitzung, kein User-Site-Ordner – keine Fehlalarme erwartet. Restlücken: R liest
  `.Rprofile` auch aus dem aktuellen Verzeichnis, Julia `~/.julia/config/startup.jl` (beide selten
  installiert); Shell-Code (`sh -c`, `BASH_ENV`) bleibt bewusst erlaubt.
- 2026-10-18: Abstammung robuster (Paket 0.19.1). Nutzer-Test: beim Spielstart (Heroic, umu/Proton)
  wiederholt `pv-try-setlocale` (pressure-vessel, Container-Pfad) gesperrt. Kette nachgestellt mit der
  umu-Laufzeit auf dem Entwicklungsrechner: pv-adverb → srt-bwrap → Starter ist lückenlos;
  pv-try-setlocale wird von `pv-locale-gen` (Shell-Skript) in schneller Folge aufgerufen und ist sehr
  kurzlebig. Vermutete Ursache: Bei vielen Starts auf langsamer Hardware ist ein Glied der Kette bei der
  Prüfung schon beendet. Änderungen: Kette jedes erlaubten Prozesses merken, dessen Elternkette gelesen
  wurde (nicht nur, wenn sie nötig war); beendeter Elternprozess → dessen gemerkte Kette; gemerkte Ketten
  beendeter Prozesse eine Scan-Runde länger behalten; Elternkette im Journal bei jeder Sperre (Diagnose).
  Getestet: Agent 158 (kurzlebiges Kind nach beendetem Shell-Elternprozess erlaubt, ohne Heroic-Freigabe
  gesperrt). Ursache auf dem Testrechner nicht bestätigt.
- 2026-10-18: Eigene Programme (Server + Agent 0.20.0). Wunsch des Nutzers: Programme wie git selbst
  hinzufügen (Dialog, Reiter „Eigene Programme“). Getestet: Server 72 (Pfad-Validierung inkl. `..`, `//`,
  Leerzeichen, Shell-Zeichen; Hinzufügen → Policy, Abwählen behält Eintrag, Fehler im offenen Dialog,
  Entfernen → Freigabe weg, 404 bei unbekannter ID, CSRF), Agent 159 (Paket-Ordner für eigene Programme
  nachberechnet, `git-remote-https` erlaubt). Neue Tabelle gegen Kopie der Entwicklungs-DB per `init_db`
  angelegt. Sichttest Entwicklungsserver (Firefox headless, hell/dunkel): Gruppe, Dialog (modal per
  JavaScript, ohne JavaScript über `?add_program=1`), Entfernen über die Oberfläche.
- 2026-10-19: Container-Prozesse nach echter Datei prüfen (Paket 0.21.0). Anlass: Mit Namespaces
  (bwrap/unshare, beide für Kinder ausführbar) ließen sich eigene Dateien unter Pfaden freigegebener
  Programme einblenden; der Agent prüfte nur den Pfad. Getestet: Agent 164 (gleiche Datei im Container
  erlaubt; andere Datei, ersetztes Skript, ersetzte Electron-App gesperrt; ersetzte App gilt nicht als
  Launcher; Laufzeit-Container von Heroic erlaubt, ohne Freigabe gesperrt). Gegentest mit echten
  unprivilegierten Namespaces auf dem Entwicklungsrechner: unveränderte Systemdatei erlaubt, andere Datei
  unter demselben Pfad gesperrt.
- 2026-10-19: Nutzer-Test auf dem Testrechner bestätigt: Heroic mit GOG-Spiel (Proton/umu) startet bei aktiver Programmkontrolle problemlos, keine Einträge unter „Gesperrte Aktionen“ (auch kein `pv-try-setlocale` mehr).
- 2026-10-19: Nutzer-Test auf dem Testrechner bestätigt: eigene Programme `git` und `fastfetch` über „Programm hinzufügen“ freigegeben, beide funktionieren bei aktiver Programmkontrolle.
- 2026-10-19: Protokollseite für gesperrte Aktionen (nur Server). Wunsch des Nutzers: durchsuchbar,
  nach Tagen, Filter Programme/Websites. Getestet: Server 76 (Art, Zeitraum inkl. Tagesgrenze in
  Ortszeit, Status, Benutzer, Suche groß/klein und mit `%`/`_`, ungültige Parameter, Reihenfolge,
  Nachladen, Häufigste, Kind-Seite nur 10 Einträge, fremde Einträge unsichtbar). Sichttest
  Entwicklungsserver (Firefox headless) mit vorübergehenden Beispieleinträgen: hell, dunkel, 420 px.
- 2026-10-19: Protokoll gesperrter Aktionen nach 90 Tagen löschen (Nutzer-Entscheidung). Server-Aufgabe
  `_prune_log_forever` (Start + alle 6 h), Zeitraum „Alles“ heißt jetzt „Alles (90 Tage)“. Getestet:
  Server 77 (älter als 90 Tage gelöscht, jüngere bleiben, auch Einträge ohne Kind; zweiter Lauf löscht
  nichts); Entwicklungsserver startet die Aufgabe ohne Fehler.
- 2026-10-19: Spielzeit je Spiel und „spielt gerade“ (Server + Agent 0.22.0). Getestet: Agent 168
  (Erkennung Steam/Heroic inkl. ungültiger IDs, Namen aus Manifest/Heroic-Bibliothek/legendary
  `installed.json`, Steuerzeichen entfernt, Datei-Lesen lehnt Symlink/FIFO/zu groß ab, Zählung für das
  Spiel statt Steam, Umgebung nur bei Kandidaten und zwischengespeichert, Namen persistiert), Server 79
  (Ende-zu-Ende über WebSocket: Statistik mit Launcher-Kennzeichnung, Namen HTML-escaped und ohne
  Steuerzeichen, „spielt gerade“ in Übersicht/Kind-Seite, endet bei leerer Meldung und nach Ablauf,
  fremde User ignoriert, alter Agent ohne `playing` akzeptiert). Namen gegen echte Heroic-/Steam-Dateien
  des Entwicklungsrechners nachgeschlagen (Cyberpunk 2077, Nobody Wants to Die). Migration
  `app_usage.name` gegen Kopie der Entwicklungs-DB. Sichttest Statistik (Firefox headless).
  Nicht getestet: echtes Spiel auf dem Testrechner.
- 2026-10-19: Nutzer-Test auf dem Testrechner bestätigt: Spielzeit je Spiel und „spielt gerade“ funktionieren.
- 2026-10-19: Protokoll-Reiter „Spiele“ (nur Server). Wunsch des Nutzers. Getestet: Server 81
  (Sitzung über WebSocket angelegt; minütliche Meldungen ergeben eine Sitzung, Lücke > 3 min eine neue;
  parallele Spiele, doppelte Einträge, fremde User und Nicht-Spiel-Schlüssel ignoriert; Meistgespielt
  sortiert und summiert; Suche; laufende Sitzung; Sperr-Ansicht unverändert; Löschung nach 90 Tagen).
  Neue Tabelle per `create_all` auf der Entwicklungs-DB angelegt. Sichttest mit vorübergehenden
  Sitzungen: hell, dunkel, 420 px (Handy: „Zuletzt“ ausgeblendet, kompakte „läuft“-Pille).
- 2026-10-19: Nutzer-Test auf dem Testrechner bestätigt: automatische Abmeldung bei abgelaufener Bildschirmzeit funktioniert.
- 2026-10-19: Nutzer-Test auf dem Testrechner bestätigt: Login-Sperre nach Zeitende greift (erneute Anmeldung abgewiesen, Hinweistext wird angezeigt).
- 2026-10-19: Nutzer-Test auf dem Testrechner bestätigt: Nach „Zeit gutschreiben“ ist die Anmeldung wieder möglich (Login-Sperre aufgehoben).
- 2026-10-19: Eltern-Profil und Konto-Wiederherstellung (nur Server). Entscheidungen des Nutzers:
  verlorener Authenticator wird von einem anderen Elternteil zurückgesetzt (sonst CLI), Benutzername und
  Anzeigename im Profil änderbar. Umsetzung: Profil (`/profile`: Benutzername, Anzeigename, E-Mail –
  Benutzername/E-Mail nur mit aktuellem Passwort; Passwort ändern; neuer Authenticator mit Bestätigung per
  Code, alter gilt bis dahin); „Passwort vergessen?“ per E-Mail-Link (30 min, einmalig, nur Hash
  gespeichert, setzt nur das Passwort, gleiche Antwort ob Account existiert oder nicht, 5 Anfragen je
  15 min und IP); „Authenticator zurücksetzen“ unter „Eltern“ (alter sofort ungültig, Sitzungen beendet,
  Einrichtungslink 24 h, verlangt Passwort, wird nicht gemailt); CLI `reset-password`/`reset-totp`;
  Sicherheitshinweise per Mail; andere Sitzungen enden nach Passwort-/Authenticator-Änderung. SMTP über
  `.env` (`SMTP_HOST/PORT/USER/PASSWORD/FROM/SECURITY`). Neue Spalte `parent.email`, Tabelle `parent_token`.
  Getestet: Server 87 (Reset-Ablauf inkl. falscher/zu kurzer Wiederholung, Einmaligkeit, altes Passwort
  ungültig, Hinweis-Mail; keine Mail ohne Adresse; Rate-Limit; ohne SMTP kein Formular; Profil inkl.
  Passwortpflicht, ungültiger/doppelter Name, ungültige E-Mail; Passwortwechsel beendet nur andere
  Sitzungen; Authenticator-Wechsel; Zurücksetzen durch anderes Elternteil inkl. Link-Einmaligkeit, alter
  Code ungültig, eigener Account abgelehnt). Mailversand gegen lokalen SMTP-Testserver (AUTH, UTF-8);
  STARTTLS/SSL nicht getestet. Migration gegen Kopie der Entwicklungs-DB. Sichttest Profil, Eltern,
  Anmeldung (Firefox headless).
- 2026-10-20: Nutzungsbericht per E-Mail (wöchentlich/monatlich, je Elternteil im Profil aktivierbar,
  HTML-Mail je Kind; siehe „Auswertung & Alarme“). Nur Server; Migration `parent.report_interval`,
  `parent.report_sent`. Getestet: Server-Tests 93 grün (neu: Zeiträume inkl. Jahres-/Monatswechsel,
  Fälligkeit vor/nach 07:00, Nachholen, kein Doppelversand, Wiederholung nach Fehlschlag erst nach 1 h,
  Inhalt – Tageswerte, Vorzeitraum, Limit inkl. Gutschrift, Programme/Spiele, Sperren ohne Audit,
  Anfragen, Zeitraumgrenzen –, HTML-Escaping vs. Textteil, Profil-Einstellung inkl. CSRF, ohne Adresse
  abgelehnt, Vorschau, Jetzt senden). Sichttest Wochen- und Monatsbericht mit Entwicklungs- und
  synthetischen Daten (2 Kinder, 70 Tage) sowie Profilseite (Firefox headless). Versand an echte
  Postfächer/Mailprogramme nicht getestet.
- 2026-10-20: Nutzer-Test Nutzungsbericht: „Jetzt senden“ über den echten SMTP-Server, Mail kommt an und
  wird korrekt dargestellt.
- 2026-10-20: Fehlsperren beim Plasma-Start beseitigt (Agent 0.23.0) (Meldung Nutzer: `kdeconnectd`, `obexd`,
  `arch-update-tray`, `srt-logger`, `which`, `ld-linux-x86-64.so.2`, `socat1` bei jedem Login).
  Ursachen laut Journal: Autostart-/User-Dienste außerhalb der Plasma-Paketkette; `srt-logger` an
  `systemd --user` umgehängt (Subshell von `steam.sh` beendet) und `which` mit beendetem
  Elternprozess; `ldd` aus einem Plasma-Widget. Änderungen: Sitzungsdienste als Basis (siehe
  Programm-Whitelist), fork-Abstammung, `ldd`-Prüfmodus, `/usr/bin/which` in der Basisliste,
  Aufrufzeile im Journal bei jeder Sperre. `socat1` (bash ← systemd --user) bleibt gesperrt, Herkunft
  noch offen. Getestet: Agent-Tests 176 grün (neu: Dienstdateien inkl. Hidden, Desktop-Actions,
  env-Präfix, fremder Besitzer/fremd-beschreibbar; Filter wie Basis; Loader nur im Prüfmodus;
  Netlink-fork-Parser ohne Threads; umgehängter `srt-logger` erlaubt, ohne fork-Wissen bzw. mit
  wiederverwendeter PID gesperrt; begrenzte fork-Tabelle; Container-Ausschlüsse im Paket). Probe auf
  dem Entwicklungsrechner (nur lesend): +10 Programme aus Sitzungsdiensten (u. a. kdeconnectd,
  kded5, mpris-proxy), keine Apps/Shells/nspawn. Netlink-fork-Events nicht auf echtem System getestet.
- 2026-10-20: Freigabe-Anfragen für gesperrte Programme und Websites (Server + Agent/Tray 0.24.0).
  Entscheidungen des Nutzers: Antworten „Dauerhaft / Nur heute / Ablehnen“, Websites über eine Liste
  im Tray, Grund optional. Protokoll abwärtskompatibel ergänzt (`access_request`, Felder an
  `chat_message`, `temp_grants` in der Policy; Version bleibt 1). Getestet: Server 98 grün (neu:
  Schlüsselprüfung, Duplikate/offene Anfrage, ungültiger Schlüssel, Ablehnen, doppelte Entscheidung,
  dauerhaft für Katalog-App/eigenes Programm/Website inkl. Entfernen der eigenen Sperre, „nur heute“
  in der Policy und nur am Tag gültig), Agent 182 grün (neu: Hauptdomain, Sperrliste inkl. Zählung und
  DoH-Ausschluss, Tray-Anfrage mit Grund, „schon angefragt“, nicht anfragbarer Schlüssel, Status aus
  Chat, Dialog aus der Benachrichtigung, Meldung bei Entscheidung, Ablauf um Mitternacht, nicht
  freigebbare Sperren), Tray 8 grün mit PySide6 in temporärer venv (neu: Liste, Dialog, Verlauf).
  Sichtprüfung Tray-Fenster hell/dunkel (offscreen) und Chat-Seite im WebUI (Firefox headless).
  Auf echtem Kinderrechner nicht getestet.
- 2026-10-20: Nutzer-Test Freigabe-Anfragen (0.24.0) auf dem Kinderrechner: Anfrage, Entscheidung und Freigabe funktionieren.
- 2026-10-20: Spiele-Limit (Server + Agent/Tray 0.25.0; siehe „Spiele-Limit“). Entscheidungen des
  Nutzers: ein Gesamtlimit je Tag, Steam/Heroic + Gruppe „Spiele“ ohne Launcher, Vorwarnung und
  Schließen, mehr Spielzeit über den Verlängerungsdialog. Protokoll abwärtskompatibel ergänzt (Felder
  mit Standardwerten). Migration: `weekday_rule.game_minutes`, `day_exception.extra_game_minutes`,
  `usage_record.game_seconds`, `message.request_target`. Getestet: Server 101 grün (neu: Wochenvorlage
  mit/ohne Spiele-Limit, ungültige Eingabe, Spielzeit-Gutschrift nur mit Limit und addierend,
  Spielzeit-Anfrage inkl. ungültiger Anfrage, Genehmigung ignoriert Fensterende, Policy-Felder,
  Spielzeit-Meldung mit Maximum-Regel), Agent 189 grün (neu: Limit inkl. Gutschrift, getrennte
  Spielzeit-Zählung, Vorwarnung nur bei laufendem Spiel, SIGTERM/SIGKILL, neu gestartete Spiele,
  gedrosselte Meldung, Warnungen nach Gutschrift wieder scharf, Launcher kein Spiel, Restzeit inkl.
  andere Geräte, Tray-Status, Spielzeit-Anfrage, Meldung bei Genehmigung, Schließen nur bei laufender
  Bildschirmzeit), Tray 9 grün mit PySide6 in temporärer venv (neu: Dialog mit Wahl Bildschirm-/
  Spielzeit, Statuszeile). Migration gegen Entwicklungs-DB (Server-Start), Sichtprüfung Kind-Seite
  (Firefox headless) und Anfrage-Dialog (offscreen). Auf echtem Kinderrechner nicht getestet.
- 2026-10-21: Internationalisierung Deutsch/Englisch (Server + Agent/Tray 0.26.0; siehe „Sprache /
  Internationalisierung“). Entscheidungen des Nutzers per Rückfrage: Umfang (WebUI, Mails, Push, alles
  beim Kind; Logs/CLI deutsch), Sitzungssprache des Kindes, Rückfall Englisch, Browsersprache mit
  Profil-Auswahl und Umschalter, zuletzt erkannte Sprache für Mails/Push, Codes im Sperrprotokoll,
  englische Programmnamen, Formate mit Monatsnamen/24 h, Deutsch als Ausgangssprache, Begriffsliste,
  kurze englische README. Protokoll abwärtskompatibel ergänzt (`name_en`, `detail_code`, `detail_arg`).
  Migration: `parent.language`, `parent.detected_language`, `device_app.name_en`,
  `blocked_event.detail_code/detail_arg`. Getestet: Server 110 grün (neu: Katalog vollständig/keine
  verwaisten Einträge, Sprachregeln, Formate, .po-Leser, Browsersprache, Umschalter inkl. Schutz gegen
  fremde Weiterleitung, Profilsprache und erkannte Sprache, alle Hauptseiten auf Englisch ohne deutsche
  Reste, Nutzungsbericht/„Passwort vergessen“/Push in der Sprache des Empfängers), Agent 196 grün (neu:
  Katalog vollständig, /etc/locale.conf, Sitzungssprache mit Zwischenspeicher und Rückfall, englische
  Spielzeit-Meldungen, Codes im Sperrprotokoll, englischer Programmname), Tray 12 grün mit PySide6 in
  temporärer venv (neu: Katalog, Sprachwahl, englische Statuszeilen). Bestehende Tests nutzen Deutsch
  über `Accept-Language`. Sichtprüfung Englisch: Kind-Seite, Statistik, Protokoll, Profil, Anmeldung,
  Nutzungsbericht (Firefox headless) sowie Sperrliste, Anfrage-Dialog und Chat der Tray-App (offscreen).
  Migration gegen Entwicklungs-DB (Server-Start). Auf echtem Kinderrechner nicht getestet.
- 2026-10-21: Nutzer-Test Spiele-Limit (0.25.0) auf dem Kinderrechner: Vorwarnung erscheint, danach wird das Spiel geschlossen.
- 2026-10-21: Nutzer-Test Spielzeit-Anfrage (0.25.0): Anfrage aus dem Verlängerungsdialog, Genehmigung im Chat funktioniert.
- 2026-10-21: Nutzer-Test WebUI auf Englisch (Server mit i18n) funktioniert. Kinderrechner auf Englisch noch nicht getestet.
- 2026-10-21: Nutzer-Test Englisch beim Kind (0.26.0): Tray und Chat englisch, Benachrichtigungen des Agenten aber deutsch. Ursache: Der Agent las die Sprache auch aus startplasma-wayland, dessen Startumgebung noch die Systemsprache enthält (Plasma setzt LANGUAGE erst danach), und LANGUAGE hatte zu niedrigen Vorrang. Behoben in 0.26.1: Vorrang wie gettext (LANGUAGE vor LC_ALL/LC_MESSAGES/LANG, außer Locale C/POSIX), Sitzungsprozesse in fester Reihenfolge (Tray-App, plasmashell, ksmserver, kwin_wayland), startplasma-* ignoriert. Getestet: Agent 196, Server-i18n 9, Tray 12 grün (neu: Plasma mit deutschen Formaten und englischer Sprache, C-Locale, startplasma mit Systemsprache, Tray-App als Python-Skript erkannt).
- 2026-10-21: Nutzer-Test 0.26.1: Benachrichtigungen des Agenten beim Kind jetzt auf Englisch (Plasma-Sprache Englisch, System Deutsch). Internationalisierung damit vollständig bestätigt.
- 2026-10-21: `socat1`-Sperre beim Login geklärt (Journal mit Aufrufzeile): `pam_kwallet_init` aus kwallet-pam
  übergibt per `socat STDIN UNIX-CONNECT:/run/user/<uid>/kwallet5.socket` das Login-Passwort an KWallet;
  die Sperre verhinderte das automatische Entsperren der Brieftasche. Agent 0.26.2 erlaubt nur genau diesen
  Aufruf mit pam_kwallet_init als Vorfahr. Getestet: Agent 197 grün (neu: erlaubt nur von pam_kwallet_init,
  nicht ohne Vorfahr, nicht mit anderem Ziel, fremdem Socket oder zusätzlichen Argumenten).
- 2026-10-21: Nutzer-Test 0.26.2: keine Sperrmeldungen mehr beim Plasma-Login (KWallet-socat, Sitzungsdienste, srt-logger, which, ldd).
- 2026-10-21: Datenbank-Backup und Hinweis auf veraltete Agenten (nur Server). Entscheidungen des Nutzers:
  täglich 03:00 mit 14 Tagen Aufbewahrung; Backup im WebUI sichtbar mit Alarm; veralteter Agent als
  Hinweis in Übersicht/Geräteliste/Geräteseite plus einmal Push je Gerät und Version, wenn das Paket seit
  einem Tag im Repository liegt (`device.update_alerted`; neueste Version aus den Paketdateien in
  `<data>/repo`, numerischer Vergleich). Getestet: Server 115 grün (neu: Schnappschuss gültig/entpackbar
  mit Inhalt, Rechte 0600, Aufbewahrung 14, Fälligkeit ab 03:00, Fehlschlag mit einmaligem Alarm,
  veraltetes Backup mit einmaligem Alarm, Einstellungsseite und „Jetzt sichern“, neueste Version aus dem
  Repo inkl. 0.26.10 > 0.26.2 und Signatur-/Datenbankdateien ignoriert, Hinweis in WebUI, Push nur einmal
  und erst nach einem Tag). Sichttest Einstellungsseite mit echtem Backup des Entwicklungsservers.
- 2026-10-21: Backup-Download im WebUI (Einstellungen): Auswahl des Schnappschusses, erneute Eingabe des eigenen Passworts (Rate-Limiting wie Login), nur vorhandene Dateien, Log-Eintrag. Getestet: Server 116 grün (neu: falsches Passwort, fremder Dateiname, gültiger Download ist gzip-SQLite).
- 2026-10-21: Nutzer-Test auf dem echten Server: Datenbank-Backup, Hinweis auf veraltete Agenten und Backup-Download im WebUI funktionieren.
- 2026-10-21: Nutzer-Test auf dem echten Server: Mailversand über SMTP mit SSL funktioniert und mit STARTTLS.
- 2026-10-21: Nutzer-Test noexec-Spieleordner (Steam): gesperrt `/srv/steam/<kind>` mit `noexec,nosuid,nodev`, Start einer Datei daraus → „Permission denied“, `LD_PRELOAD` einer Bibliothek daraus → „failed to map segment“; nach Freigabe sofort ausgehängt. Heroic gesperrt: `/srv/heroic/<kind>` ebenfalls `noexec`.
- 2026-10-21: GNOME-Unterstützung (Paket 0.27.0, per /grill-me abgestimmt: Plasma und GNOME auch auf einem
  Rechner, Tray per AppIndicator plus Eintrag „KidCTL“, Split-Pakete `kidctl`/`kidctl-plasma`/`kidctl-gnome`,
  erst VM, dann echter Rechner). Details unter „Desktop-Umgebungen“. Bei der Umsetzung ergänzt: Hinweis der
  Login-Sperre unter GDM per `pam_nologin` (GDM zeigt keine PAM-Infotexte), gjs als Interpreter, Sitzungs-
  dienste unter `/usr/share`, Dienste beim Login ohne Popup, Abschalten der GNOME-Suchanbieter nicht
  freigegebener Apps, Integritätsprüfungen Desktop-Paket, Tray-Symbol und GDM-Autologin. Getestet: Agent 209,
  Server 116, Tray 13 Tests grün (neu u. a. Abmeldung je Desktop inkl. unbekanntem Desktop, Sprache aus
  gnome-shell/AccountsService, Basisliste/Pakete, Login-Dienste, Suchanbieter, `--show`/eine Instanz,
  `tray_icon`). VM (Arch, GNOME 51 + GDM neben Plasma 6): Pakete gebaut; Update nur von `kidctl` zeigt
  Hinweise auf beide Desktop-Pakete; Schema-Override schaltet AppIndicator ein. Audit-Login unter GNOME
  zeigte 5 Programme (gjs-Dienste von GNOME Shell, AppIndicator-Werkzeug, GNOME Software) – danach im
  Modus „Sperren“ Login ohne Popup (Software still gesperrt). Tray-Symbol sichtbar, „KidCTL“ über die
  GNOME-Suche öffnet das Fenster der laufenden Tray-App; Erweiterung abgeschaltet → Integrität „Tray-Symbol
  … nicht sichtbar“. Zeitende: Vorwarnung, Countdown, Abmeldung über GNOME ohne hartes Beenden, GDM zeigt
  den Anmeldebildschirm; gesperrter Login in GDM zeigt den Hinweis (zuvor nur „Sorry, password
  authentication didn't work“), SDDM zeigt ihn weiter (Theme), `pamtest.py`: login/sshd/sddm Infotext,
  gdm-password/-autologin Fehlermeldung, admin erlaubt. GDM-Autologin eines Kindes bei Sperre → schwarzer
  Bildschirm (auch ohne kidctl) → Integritätsmeldung im WebUI. Kontosprache Englisch → Benachrichtigung
  „App blocked“ englisch. Suche nach „weather“ ohne Sperren; Freigabe „Dateien“ nimmt ihren Suchanbieter
  aus der Liste. Plasma auf demselben Rechner: Login ohne Sperrmeldungen, Abmeldung über Plasma
  (`Desktop KDE`) ohne hartes Beenden. Nicht getestet: echter Rechner, Tray-Menü per Maus unter GNOME,
  „Freigabe anfragen“-Knopf in GNOME-Benachrichtigungen.
- 2026-10-21: Debian/Ubuntu, Schritt 1 (Paket 0.28.0, per Rückfragen abgestimmt: Ubuntu 24.04 & Co., Debian 13,
  Ubuntu 26.04; Desktops GNOME/Plasma jetzt, Cinnamon und COSMIC als eigene Schritte; Bibliotheken mitbringen
  nur wo nötig, Tray mit PyQt6-Rückfall; signiertes APT-Repo; Snap wie Flatpak). Details unter
  „Debian/Ubuntu“. Bei der Umsetzung ergänzt bzw. gefunden: PAM-Priorität vor sss, AppArmor beim Resolver,
  `/usr/sbin` im PATH, ausstehende dpkg-Trigger, Essential-Pakete, dbus-daemon in der Login-Regel,
  noexec-Sicht des Systems, Nautilus unter Ubuntu immer erlaubt, `plasma-workspace-wayland`,
  KWallet-Skript und kconf_update unter Ubuntu, SDDM-Theme mit `import QtQuick 2.15` (Qt 5), neutrale
  Update-Texte im WebUI. Getestet: Agent 217, Server 117, Tray 13 Tests grün (Tray zusätzlich komplett mit
  PyQt6). VM Ubuntu 24.04: Pakete mit echtem dpkg installiert, PyQt6 als Ersatz für PySide6 gezogen,
  mitgebrachtes pydantic 2.14/websockets 17 geladen; PAM-Test (`pamtest.py`): login/sshd Infotext,
  gdm-password/-autologin Fehlermeldung, admin erlaubt; Webfilter über Ubuntus Stub-Resolver
  (example.com ja, pornhub.com nein – auch im Snap-Firefox, dort DoH per Policy gesperrt); GNOME-Login ohne
  Popup (Audit zuvor: grep, Migrationsskripte, gio-launch-desktop, ibus, apport, Nautilus); Snap-Firefox
  gesperrt bzw. nach Freigabe `snap:firefox` lauffähig; Zeitende: Abmeldung über GNOME, GDM zeigt den
  Sperrhinweis; Integrität im WebUI vollständig grün; Steam aus `steam-installer`: `/srv/steam/kind`,
  Verweis `~/.steam/debian-installation`, noexec ohne Freigabe; APT-Repo mit Testschlüssel: Einrichtung per
  `kidctl-repo-client.sh`, `apt` prüft die Signatur und installiert daraus. Plasma (Wayland) zusätzlich in
  derselben VM: SDDM-Theme unter Qt 5, Login ohne Sperren, Tray, Abmeldung über Plasma, Sperrhinweis in
  SDDM. Debian 13 und Ubuntu 26.04 (je VM ohne Oberfläche): Installation mit Systempaketen (pydantic
  2.10/2.12, PySide6), Agent startet, Kopplung und Policy; `apt remove`/`purge` nimmt PAM, Dienst und
  Zustand zurück. Arch-VM: Agent mit neuer Paketdatenbank-Schicht, SDDM-Theme unter Qt 6 unverändert.
  Nicht getestet: echte Rechner, Steam-Spiel unter Ubuntu, Heroic-.deb, Kubuntu mit X11 (Standard von
  Kubuntu 24.04).
- 2026-11-04: Windows 10/11 als Client per /grill-me abgestimmt (noch nicht umgesetzt): Zielsysteme, Python mit
  gemeinsamem Kern, Login-Sperre per Konto-Deaktivierung mit Abmelde-Rückfall, Ausführverbote per ACL mit
  Ausnahme je freigegebener App, Basisliste `%WINDIR%` ohne Code-Ausführer, Website-Filter mit Profil der aktiven
  Sitzung und rechnerweit erzwungenem lokalem Resolver, Spiele (Steam, Epic, Xbox/Store, neuer Schalter „zählt
  als Spiel“), Toasts plus Vordergrund-Fenster, Installer ohne Signatur mit Ed25519-geprüftem Selbst-Update,
  Test-VMs Windows 11 Home und Windows 10 Pro, Umsetzung in vier Schritten. Details unter „Windows“.
- 2026-11-04: Windows Schritt 1 umgesetzt (Paket 0.29.0): Bildschirmzeit, Abmeldung, Login-Sperre über die
  Konto-Deaktivierung, „Jetzt sperren“, Tray-App mit Chat, Toasts (Knopf über `kidctl://`) und Countdown-Fenster,
  Integrität (abgesicherter Modus, Autostart, Tray-Symbol, Kinder ohne Adminrechte), Installer (Inno Setup,
  eingebettetes Python 3.14, `packaging/windows`), automatisches Selbst-Update mit Ed25519-Signatur, Test-VMs
  (`dev/vm/winvm.sh`). Agent: Kern als `AgentCore`, Linux unverändert. Server: Plattform je Gerät (Migration
  `device.platform`), Windows-Installer unter `/repo/windows/`, Update-Hinweise je Plattform. Bei der Umsetzung
  ergänzt: Agent startet fehlende Tray-Apps in Kinder-Sitzungen, Anmeldebildschirm wird nach Sperränderungen
  aktualisiert, `TrayState.logout_in_seconds`, `Hello.platform` (abwärtskompatibel). Getestet: Agent 237,
  Server 121, Tray 18 Tests grün (neu u. a. Windows-Teile mit Attrappen: Konten/SID/RID, Login-Sperre inkl.
  Neustart und fremd deaktivierter Konten, Sitzungen, Benachrichtigungen/Knöpfe, Update-Signatur und
  -Prüfsumme, Tray-Neustart, Import ohne POSIX-Module, Countdown, Toast-XML, Migration). VM Windows 11
  (25H2, Pro – Home noch nicht): stille Installation, Rechte auf `ProgramData\KidCTL`, Dienst inkl. Stop/Neustart
  und Wiederherstellung durch Windows, Kopplung, Konten im WebUI, Tray-App mit Restzeit und Chat, Toast bei
  Eltern-Nachricht (unter „Bitte nicht stören“ in der Mitteilungszentrale), Zeitende: Warnfenster und Toast in der
  letzten Minute, Countdown-Fenster, Abmeldung nach 60 s, Konto deaktiviert; Gutschrift aktiviert es wieder;
  „Jetzt sperren“ (Fenster „Gesperrt“ ohne Verlängerungsknopf) und Freigabe im Countdown; Knopf im Toast öffnet den
  Anfrage-Dialog, Anfrage kommt beim Server an; Selbst-Update 0.29.0 → 0.29.1 → 0.29.2 (Tray-App danach automatisch
  neu gestartet); Deinstallation aktiviert das gesperrte Konto und entfernt alles. Dabei gefunden und behoben:
  Linux-Import im Windows-Agenten, `icacls … /T` ließ beim Update Dateien ohne Rechte zurück, Laufzeitdateien
  blieben nach der Deinstallation, Konsolenausgabe von `kidctl status`, SSH-Firewall der Test-VM. Nicht getestet:
  Windows 11 Home, Windows 10, echter Rechner, Kopplungsseite im Installer (nur https), Microsoft-Konto,
  abgesicherter Modus, Benutzerwechsel mit zwei angemeldeten Kindern.
- 2026-11-05: Windows Schritt 2 umgesetzt (Paket 0.30.0): Programmkontrolle mit Katalog (Startmenü, Store-Apps),
  Regeln (`win/programs.py`), Prozessliste alle 0,5 s, Beenden mit Toast und „Freigabe anfragen“, Sperrprotokoll,
  Ausführverbot per ACL mit Ausnahme je freigegebener App im Profil, Integrität `acl:<user>`. Server: Schlüssel
  `appx:` und Windows-Pfade (Freigaben, Anfragen, eigene Programme), neue Sperrgründe (eigene Programmdatei,
  Systemwerkzeug, fremde DLL). Kern: Freigabe-Anfragen in `AgentCore` (Linux unverändert). Abweichung von der
  Planung: Prozessliste statt ETW. Bei der Umsetzung ergänzt: Regel „Programmdatei gehört dem Kind“, Basis für
  `MicrosoftWindows.Client.*`, Windows-Helfer mit App-Identität, Defender-Plattform und von Aufgabenplanung/
  Diensten gestartete Programme (Audit in der VM), feste Namen für mitgelieferte Store-Apps, `dev/vm/vmctl.py`,
  `dev/vm/winsync.sh`. Getestet: Agent 247, Server 123, Tray 18 Tests grün (neu u. a. Regeln, Katalog,
  Watcher mit Sperren/Protokoll/Freigabe, ACL-Abgleich, SDDL-Prüfung, Server-Schlüssel). VM Windows 11: Audit
  beim Login (nur Systemteile, danach in die Basis aufgenommen), im Modus „Sperren“ Edge, Rechner, Task-Manager,
  Terminal gesperrt mit Toast; Programm im Download-Ordner von Windows abgewiesen (ACL); Freigabe-Anfrage aus der
  Tray-App, „nur heute“ erlaubt -> Rechner startet; App im Profil (gehört dem Kind) erst abgewiesen, nach
  Freigabe lauffähig (Ausnahme am App-Ordner); Integrität grün; Programmkontrolle aus bzw. `unlock-all` nimmt alle
  Einträge zurück (Profil wieder Vollzugriff – dabei gefunden und behoben). Nicht getestet: Store-Installation
  durch das Kind, Microsoft-Konto, Windows 10, Windows 11 Home, echter Rechner.
- 2026-11-06: Windows Schritt 3 umgesetzt (Paket 0.31.0): Website-Filter mit Unbound (mitgeliefert, vendor.lock),
  Profil der aktiven Sitzung, Adapter auf den lokalen Resolver (Originale gemerkt und wiederhergestellt), WFP-Filter
  je Kind (Port 53/853, Netzsperre im Hintergrund) in einer dynamischen Sitzung, Richtlinien gegen DoH (Installer),
  Integrität, Sperrprotokoll und Website-Freigaben. Gemeinsam: Abruf der Sperrlisten als Modulfunktion
  (`dnsfilter.fetch_lists`), Linux unverändert. Abweichungen: WFP statt Firewall-Regeln (wirken ausgehend nicht je
  Benutzer – in der VM gezeigt), keine rechnerweite Port-53-Sperre. Getestet: Agent 251, Server 123, Tray 18 Tests
  grün (neu: Unbound-Konfiguration, Adapter umstellen/merken/wiederherstellen, Profile und Sperrprotokoll,
  WFP-Regeln je Sitzung). VM Windows 11: WFP sperrt DNS (53, UDP) und DoT (853) eines Benutzers, HTTPS bleibt;
  „alles“ sperrt auch HTTPS; nach dem Schließen der Sitzung alles frei. Mit Kind vorne: Adapter auf 127.0.0.1,
  `tiktok.com` (Eltern-Sperre) und `dns.google` (DoH) NXDOMAIN, `www.google.com` -> `forcesafesearch.google.com`,
  `example.com` erreichbar, Einträge im Sperrprotokoll; Benutzerwechsel zum Admin: ungefiltert, Kind im Hintergrund
  mit Netzsperre (Filter in `netsh wfp`); zurück zum Kind: Filter wieder aktiv, Netzsperre weg; Dienst durch Admin
  gestoppt: Adapter zurück auf DHCP (10.0.2.3), nach dem Start wieder auf den Filter. Dabei gefunden und behoben:
  gleichzeitige Abgleiche, Übergabe der gemerkten Adapter an PowerShell, GUID-Format von `Get-NetAdapter`. Nicht
  getestet: Whitelist-Modus und Sperrlisten unter Windows (Konfiguration wie Linux), VPN, Browser mit eigenem
  DNS, Windows 10, echter Rechner.
- 2026-11-06: Windows Schritt 4 umgesetzt (Paket 0.32.0): Spielerkennung Steam/Epic über die Installationsordner,
  Store-Spiele über den Katalog, Launcher-Regel (Nachfahren und Spieleordner freigegebener Launcher), Ausführverbot
  für die Steam-Bibliothek ohne Steam-Freigabe, Programm- und Spielzeit, „spielt gerade“, Spiele-Limit, Countdown-
  Fenster für die letzte Minute Spielzeit. Für alle Plattformen: Schalter „zählt als Spiel“ im WebUI (Tabelle
  `child_game_app`, `ChildPolicy.game_apps`), `is_game`/`game_remaining` im gemeinsamen Kern. Getestet: Agent 256,
  Server 124, Tray 19 Tests grün (neu: Steam-/Epic-Index mit Windows-Pfaden, Launcher-Regel, App-Zuordnung,
  Spielzeit und Spiele-Limit im Windows-Agenten, Spiel-Schalter in WebUI und Policy, Countdown für Spiele).
  VM Windows 11: Solitaire (Store-Spiel) freigegeben, Spiele-Limit 1 min – Toast „Noch 1 Minute Spielzeit“,
  Fenster „Noch 27 Sekunden Spielzeit“, Spielzeit und Programmzeit beim Server, nach Ablauf „Spielzeit
  aufgebraucht“ und Spiel geschlossen, auch beim erneuten Start. Dabei gefunden: Game Bar startet mit jedem Spiel
  (jetzt Basis). Nicht getestet: Steam und Epic mit echten Spielen (Anmeldung nötig), andere Launcher, Spiele im
  Vollbild, Windows 10, echter Rechner.
- 2026-11-06: Windows 10 getestet (VM Windows 10 Pro 22H2, Installer 0.32.0 in der Win10-VM gebaut): stille
  Installation, Kopplung, Dienst und Tray-App (Autostart, Restzeit, Chatverlauf); Nachricht als Toast; Sperre mit
  1 min Vorwarnung – Toast und Countdown-Fenster, Abmeldung nach 60 s, Konto deaktiviert und vom
  Anmeldebildschirm verschwunden, nach Freigabe wieder da; Programmkontrolle – Rechner (Store-App) geschlossen,
  Toast „Programm gesperrt“, Knopf „Freigabe anfragen“ aus dem Info-Center öffnet den Dialog über `kidctl://`,
  Anfrage beim Server, „heute erlauben“ wirkt sofort; Website-Filter (Whitelist) – example.com gesperrt,
  Wikipedia erreichbar. Keine Codeänderung nötig. Test-VM: Feature-OpenSSH von Windows 10 startet nicht →
  `provision-windows.ps1` installiert dann Win32-OpenSSH 10.0 (MSI, gepinnt) und setzt den sftp-Pfad. Bemerkt:
  Im Whitelist-Modus werden auch Windows-Dienste wie SmartScreen (`checkappexec.microsoft.com`) und Telemetrie
  gefiltert und als gesperrte Domains gemeldet. Nicht getestet: Spiele unter Windows 10,
  Update über `latest.json` (Entwicklungsserver liefert das Windows-Repo nicht aus).
- 2026-11-08: Windows-Nacharbeit (Paket 0.32.2): Abgesicherter Modus – der Agent stürzte im minimalen Modus sofort
  ab (`import asyncio`: WinError 10050, kein Netzwerk-Stack), Kinder hätten dort keine Zeitbegrenzung gehabt; jetzt
  Wächter ohne `asyncio` (`win/safemode.py`, `service.dispatch`), Kinder werden in beiden abgesicherten Modi
  gesperrt. Whitelist-Modus: feste Listen für Windows-Infrastruktur (erlaubt) und Hintergrundrauschen (still
  gesperrt). Anmeldebildschirm auch in gesperrten Sitzungen aktualisieren (ein freigegebenes Konto fehlte dort in
  „Benutzer wechseln“). Keine Tray-App mehr in einer Sitzung starten, deren Abmeldung läuft. `dev/webctl.py map`
  behält bestehende Zuordnungen. Getestet: Agent 260, Server 124, Tray 19 Tests grün. VM Windows 10
  mit echtem Installer: Selbst-Update 0.32.0 -> 0.32.1 -> 0.32.2 (Signatur, stille Installation, Agent nach etwa
  12 s zurück); Whitelist mit echtem Unbound: Systemdomains erreichbar, Telemetrie/MSN gesperrt und nicht gemeldet;
  zwei Kinder gleichzeitig: Filterprofil folgt der vorderen Sitzung, Sperre meldet die getrennte Sitzung nach 60 s
  ab, das andere Kind arbeitet weiter, freigegebenes Konto erscheint sofort in der Liste der gesperrten Sitzung;
  abgesicherter Modus minimal: Dienst läuft, beide Kinder fehlen auf dem Anmeldebildschirm, ein schon angemeldetes
  Kind bekommt den Hinweis und wird abgemeldet; mit Netzwerk: Wächter läuft; danach Normalstart: Konten wieder
  aktiv. Nicht getestet: echter Rechner, Windows 11 Home, Microsoft-Konto, Kopplungsseite im Installer,
  Store-Installation durch das Kind, Steam/Epic mit echten Spielen, VPN, Browser mit eigenem DNS.
- 2026-11-08: Sicherheitsprüfung des Gesamtsystems, alle Befunde behoben (Agent 0.32.3). Server: Größenlimit 2 MiB je
  Anfrage (`app/middleware.py`, vorher ohne Anmeldung unbegrenzt: 200 MB -> 1,3 GB Speicher); Login-Sperre nicht mehr
  je Benutzer nach 5 Fehlern (jeder konnte ein Elternteil aussperren), sondern 5 je IP, 50 je Benutzer und 5 falsche
  Codes bei richtigem Passwort je Benutzer; für IP-Sperren zählt der letzte `X-Forwarded-For`-Eintrag (von Traefik),
  nicht der vom Client mitgeschickte; Mail bei „Passwort vergessen“ im Hintergrund (Laufzeit verriet Konten);
  Content-Security-Policy ohne Inline-Skripte (Rückfragen über `data-confirm` in `app.js`); Warnung bei `SECRET_KEY`
  unter 32 Zeichen; `docker-compose.yml` mit `mem_limit`, `cap_drop: ALL`, `no-new-privileges`. Windows-Agent: Eine
  Junction im Pfad eines freigegebenen App-Ordners brachte dem Kind Leserecht auf beliebige Ordner (z. B. Geräte-Token);
  jetzt werden alle Pfadteile während `icacls` ohne Verknüpfungen geöffnet und gesperrt (`acl.pin_path`). Getestet:
  Agent 261, Server 135, Tray 19 Tests grün (neu `server/tests/test_security.py`, je Befund der ursprüngliche Angriff).
  Echter uvicorn: 200 MB mit und ohne Längenangabe -> 413, Speicher 125 statt 1286 MiB; richtiger Login trotz fremder
  Fehlversuche. VM Windows 10 mit echtem `AclManager`: Junction im Zwischenpfad -> keine Rechte am Ziel, Ordner während
  des Vorgangs nicht austauschbar (WinError 32), normale Freigabe und Rücknahme gehen. Browser: alle Seiten ohne
  CSP-Verstoß, Rückfrage bei Formular und Knopf in beide Richtungen. Geprüft ohne Befund: Zugriffsschutz aller Routen,
  CSRF, XSS, Open Redirect, Pfad-Traversal, TLS und Signaturprüfung der Updates, Abhängigkeiten (`pip-audit`), keine von
  außen erreichbaren Ports des Agenten. Nicht getestet: Traefik-Konfiguration und Host, `docker compose` (kein Docker
  auf dem Entwicklungsrechner), Web Push im Browser (Service Worker läuft im Test-Browser generell nicht).
- 2026-11-08: Repo-Skripte: Fehlt der GPG-Ordner, brach `kidctl-repo-build.sh` wegen `set -e`/`pipefail` ohne Meldung ab;
  jetzt kommt „kein Signierschlüssel“. Getestet mit leerem `KIDCTL_REPO_HOME` (Meldung, Exit 1; `key.sh status` meldet
  „Noch kein Schlüssel“) und mit Wegwerf-Schlüssel (Dry-Run läuft durch).
- 2026-11-08: Windows-Repo: `build.py sign` behält nur noch den neuesten Installer und eine ältere Version, der Rest
  wird nach dem Schreiben der Signatur gelöscht (Dry-Run listet ihn nur). Getestet mit Testordner und Wegwerf-Schlüssel:
  numerische Reihenfolge (0.9.0 vor 0.30.0), `latest.json` zeigt auf den neuesten, Signatur gültig, ein bis zwei
  Installer bleiben unangetastet; Dry-Run gegen das echte Repo löscht nichts.
- 2026-11-08: Download-Seite: `/repo/` und `/repo/windows/` listen öffentlich die Windows-Installer (neuester zuerst,
  Größe, Datum, Download-Link); andere Dateien des Repos erscheinen nicht, deren URLs bleiben unverändert.
  Getestet: Server-Tests (numerische Reihenfolge, nur Dateien nach dem Namensmuster, leerer Ordner, Sprache) und am
  Entwicklungsserver im Browser (Liste ohne Anmeldung, Links liefern den 37-MB-Installer, keine CSP-Fehler).
- 2026-11-08: Windows-Client (Paket 0.32.4): Kopplung auf frischen Rechnern scheiterte mit `CERTIFICATE_VERIFY_FAILED`, weil
  Python fehlende Stammzertifikate (ISRG Root X1) nicht nachladen kann. Neues `kidctl_agent/tls.py`: unter Windows prüft
  `truststore` über die Windows-API (Kopplung, WebSocket, Updates, Sperrlisten); Linux unverändert. Getestet auf Windows 10
  mit Installer 0.32.4 gegen `https://kids.nossco.de`, vor jedem Fall Root aus dem Speicher entfernt: Python-Standard
  scheitert wie gemeldet, alle vier Wege klappen als Administrator und als SYSTEM, die Root wird dabei nachgeladen, `enroll`
  erreicht den Server. Abgelehnt werden abgelaufenes, selbstsigniertes, nicht vertrauenswürdiges Zertifikat und falscher
  Hostname (HTTPS und WSS). Agent-Tests grün. Nicht getestet: Windows 11, echter PC.
- 2026-11-08: Windows-Programmkontrolle (Paket 0.32.5): Die App „Einstellungen“ ist Basis statt Eltern-Entscheidung; ohne sie
  ließen sich Hintergrund, Taskleiste und Infobereich nicht anpassen (die App wurde beim Start kommentarlos beendet),
  sie erscheint auch nicht mehr im Katalog. Getestet auf Windows 10 mit Installer 0.32.5 als Kind im Modus „Sperren“: vorher
  im Protokoll gesperrt, danach Hintergrund gewechselt, Taskleisten-Schalter und Dialog „Infobereichsymbole“ gehen, Rechner
  bleibt gesperrt. Änderungen am System verlangen in den Einstellungen weiter Administratorrechte; Proxy und VPN
  des eigenen Kontos kann das Kind dort einstellen. Nicht getestet: Windows 11, echter PC.
- 2026-11-08: Windows-Programmkontrolle (Paket 0.32.6): Das Ausführverbot im Profil gilt nur noch im Modus „Sperren“. Bei „Nur
  protokollieren“ ließ NTFS Programme aus dem Profil nicht starten, obwohl der Modus laut WebUI nichts sperrt (gemeldet beim
  Installieren von Steam). Getestet auf Windows 10 mit Installer 0.32.6 als Kind, Programm in `Downloads`: 0.32.5 und
  „Nur protokollieren“ Zugriffsfehler; 0.32.6 startet es und meldet „Protokolliert“, bei „Sperren“ wieder Zugriffsfehler, Verbot wird
  in beide Richtungen gesetzt und aufgehoben. Nicht getestet: echtes `SteamSetup.exe`, Windows 11, echter PC.
- 2026-11-08: Steam ist plattformübergreifend ein Programm (Agent 0.32.7, Server): Im WebUI gibt es eine Zeile „Steam“, gespeichert unter
  `exe:/usr/bin/steam`. Der Windows-Agent bezieht jede Steam-Freigabe auf sein per Registry (HKLM) erkanntes Steam samt Ordner und
  Bibliotheken; Helfer wie `steamwebhelper.exe` erscheinen im Protokoll als „Steam“, ein gesperrtes `steam.exe` löst einen neuen
  Katalog-Scan aus. Bewusst nur Steam und nicht nach Anzeigenamen: Verknüpfungen im Profil kann das Kind selbst benennen. Getestet:
  Agent- und Server-Tests (auch gefälschtes `steam.exe` wird nicht mit freigegeben), Windows 10 mit Installer 0.32.7 als Kind und
  nachgestelltem Steam: frisch installiert gesperrt und nach dem Sperren im Katalog, eine WebUI-Zeile, Freigabe über den Linux-Schlüssel erlaubt
  `steam.exe` und `steamwebhelper.exe`, Entzug beendet beide. Nicht getestet: echtes Steam, Windows 11, echter PC.
- 2026-11-09: WebUI: Die lange Kinderseite ist auf Reiter verteilt: Heute (`/children/<id>`: Rest und genutzte Zeit, erlaubte Zeitspanne, Online-Status,
  Sperren, Zeit gutschreiben, Zusammenfassung, letzte 5 Sperren), Zeiten (`/zeiten`), Internet (`/internet`), Programme (`/programme`), dazu Statistik,
  Protokoll und Chat in derselben Leiste. Speichern führt zurück auf denselben Reiter. Programme: nur Spiele und eigene Programme aufgeklappt, Filter „Nur
  erlaubte“, Speichern bleibt sichtbar, Hinweis auf ungespeicherte Änderungen, „Spiel“ nur bei erlaubten Programmen, gleichnamige Einträge unterscheidbar,
  Hilfetexte eingeklappt und passend zur Plattform des Kindes. Begriffe vereinfacht (z. B. „Zeit pro Tag“, „Nur freigegebene Websites“ statt Whitelist).
  Handy: Wochenvorlage ohne seitliches Scrollen. Statische Dateien tragen eine Prüfsumme in der URL, Updates kommen ohne Cache-Reste an. Fix: `hidden` wirkte
  bei Elementen mit eigenem `display` nicht, die Programmsuche blendete deshalb einzelne Zeilen nicht aus. Getestet: Server-Tests (neu `test_child_pages.py`)
  und im Browser am Entwicklungsserver auf 1280 px und 375 px: alle Reiter, Filter, Suche, Änderungshinweis; Seitenhöhe mit 67 Programmen von 5152 px auf
  höchstens rund 1200 px auf den Reitern Heute, Zeiten, Internet und Programme (Handy von 8663 px auf höchstens 1900 px). Nicht im Browser getestet: Absenden der Formulare (Testserver lehnt die Herkunft
  `localhost` ab, abgedeckt durch die Server-Tests).
- 2026-11-10: WebUI: Die Kopplungsseite zeigt beide Wege – Windows (Installer von `/repo/`, Server-Adresse und Kopplungscode auf der Seite
  „Kopplung“ eingeben; bei vorhandener Installation `kidctl.cmd enroll`) und Linux (`sudo kidctl enroll`). Die Benutzer-Zuordnung eines Geräts
  heißt je nach Plattform „Windows-Benutzer“ bzw. „Linux-Benutzer“, die UID-Spalte entfällt unter Windows; der Chat-Hinweis spricht von
  „Benutzerkonto“. Getestet: Server-Tests (neu `test_pair_page_and_user_mapping_name_the_platform`).
- 2026-11-10: Lizenz von PolyForm Noncommercial 1.0.0 auf AGPL-3.0-or-later umgestellt (`LICENSE`, PKGBUILD, READMEs), Voraussetzung für die
  kostenlose Code-Signatur durch die SignPath Foundation. Fußzeile der WebUI mit Link „Quellcode“ (AGPL §13, `SOURCE_URL`, Standard das
  GitHub-Repo). Vorbereitung SignPath: `docs/code-signing.md` (Code signing policy, Rollen, Datenschutz), Abschnitte in den READMEs, Link auf der
  Download-Seite; Workflow `.github/workflows/windows.yml` baut den Installer auf `windows-2025` (`build.py ci`: Inno Setup 6.7.4 aus
  `vendor.lock` mit SHA-256, Staging, ISCC), lädt ihn als Artefakt hoch und reicht ihn an SignPath weiter, sobald Secret/Variablen gesetzt
  sind; `build.py fetch` holt den signierten Installer, `sign` signiert danach `latest.json` wie bisher lokal. Öffentlicher Update-Schlüssel
  liegt jetzt in `packaging/windows/update-key.pub` (Abgleich mit dem geheimen Schlüssel). Installer mit Produktname/-version, Copyright und
  `LICENSE.txt`. Getestet: Server-Tests; Staging + Bau in der Win10-VM in einen Testordner (VersionInfo: ProductName KidCTL, Version 0.32.7,
  Lizenz und öffentlicher Schlüssel im Staging); Dry-Runs von `ci` und `fetch`; Workflow nur syntaktisch geprüft (Lauf auf GitHub steht aus).
- 2026-11-15: SignPath Foundation hat den Antrag abgelehnt (Projekt noch nicht bekannt genug). READMEs, `docs/code-signing.md`, spec-Kopf und
  `packaging/windows/README.md` sagen jetzt, dass die Windows-Installer derzeit nicht signiert sind; Bau in GitHub Actions, Releases mit SHA-256
  und Link auf den Lauf, Datenschutzhinweis und Lizenz bleiben. Workflow und `build.py ci/fetch` bleiben für einen späteren Antrag unverändert.
  Getestet: nur Doku – Verweise geprüft, keine Code-Änderung.
- 2026-11-15: Paketbau in GitHub Actions: `.github/workflows/release.yml` (vorher `windows.yml`, Name „Pakete“) baut bei Tag `v<Version>` oder
  von Hand neben dem Windows-Installer auch die Linux-Pakete (Job `linux` im Container `archlinux:base`: makepkg als Benutzer `builder`,
  `build-deb.py`; Artefakt `kidctl-linux` mit `arch/` und `deb/`, Versionsprüfung PKGBUILD/Agent/Tag). Signiert wird weiter nur lokal:
  `kidctl-repo-build.sh --apply --ci [LAUF]` holt die Pakete (Lauf zum Tag, sonst neuester erfolgreicher), signiert sie mit GPG und
  aktualisiert pacman- und APT-Repo; `build.py fetch` nimmt den signierten Installer, sonst den unsignierten. Lokaler Bau (makepkg,
  Windows-VM) bleibt möglich. Doku: `packaging/repo/README.md`, `docs/installation.md` 2.2, `packaging/windows/README.md`.
  Getestet: `--ci` mit Ersatz für `gh` und lokal gebauten Paketen gegen eine Schlüsselkopie in einem Testordner (Paket-, Datenbank- und
  InRelease-Signaturen gültig, echtes Repo unberührt); Dry-Runs von `--ci` und `fetch`; Workflow-YAML syntaktisch geprüft. Der Lauf auf
  GitHub (Container, makepkg als `builder`) steht noch aus.
- 2026-11-15: Release-Ablauf automatisiert (Schlüssel bleiben lokal): `release.yml` startet auch bei Push auf main, wenn
  `agent/kidctl_agent/__init__.py` oder PKGBUILD geändert sind; Job `version` prüft PKGBUILD/Agent, legt das Tag `v<Version>` per API an
  und gibt den Bau frei – gibt es das Tag schon, wird nichts gebaut. Neues `packaging/repo/kidctl-release.sh [--apply] [LAUF]`: wartet auf
  Tag und Lauf (bis 10 min, dann `gh run watch`), ruft `kidctl-repo-build.sh --ci`, `build.py fetch`/`sign` und `kidctl-repo-publish.sh`
  auf. Lauf-Suche in allen Skripten über den Commit des Tags (`gh run list -c`). `kidctl-repo-publish.sh` liest das Ziel auch aus
  `~/.local/share/kidctl-repo/publish-target`. Getestet: Dry-Run von `kidctl-release.sh` mit Lauf 43747182262 bis einschließlich
  Signier-Schritten; Fehlermeldungen ohne Tag/Lauf; Workflow-YAML syntaktisch geprüft. Nicht getestet: Auto-Tag auf GitHub (erst bei der
  nächsten Versionserhöhung), `rsync` zu srv02 (SSH-Anmeldung nur aus dem eigenen Terminal).
- 2026-11-15: WebUI-Design auf Material Design 3 umgestellt, mobile first: Farbrollen aus Grundfarbe Blau (`#3B5BA9`) für Hell und Dunkel
  inkl. Erfolgs-/Warnfarben, M3-Typoskala (Roboto/Systemschrift, keine Webfonts), umrandete Textfelder, Pill-Buttons (filled/outlined/text/
  tonal), Chips, Dialoge, lineare Fortschrittsbalken, primäre Reiter. Navigation: obere App-Leiste (Darstellung, Profil, Abmelden als
  Symbol-Knöpfe), unten eine Navigationsleiste mit eigenen Symbolen (`_icons.html`), ab 840 px als Leiste links. Darstellung automatisch nach
  System oder fest hell/dunkel: Knopf in der App-Leiste schaltet Auto → Hell → Dunkel, Cookie `kidctl_theme`, der Server setzt
  `data-theme`, `color-scheme` und `theme-color` (kein Aufblitzen; ohne JavaScript gilt das System). Übersicht: eine Karte je Kind mit
  Restzeit, Fortschritt und Aktionen statt Tabelle; Tabellen (Geräte, Kinder, Eltern, Gutschriften) werden auf dem Handy zu Listen
  (`table.responsive`, `data-label`). Heute-Bereich als farbige Fläche mit großer Restzeit. Manifest-Farben angepasst. Getestet: Server-Tests
  (neu `test_theme_cookie_sets_appearance_and_navigation`); im Browser auf 375 px und 1280 px in Hell und Dunkel: Übersicht, alle Reiter
  eines Kindes, Geräte, Gerät, Eltern, Einstellungen, Profil, Anmeldung – kein seitliches Scrollen, Umschalter samt Cookie und Neuladen.
- 2026-11-15: Nutzungsbericht-Mail (`mail/report.html`) im Stil der neuen WebUI: Material-3-Farben (Blau, Warnton für Tage über dem Limit),
  Roboto/Systemschrift statt Serif, keine Großbuchstaben-Labels, Karten mit 16 px und Kacheln mit 12 px Radius. Dunkles Schema für
  Mailprogramme mit `prefers-color-scheme` (z. B. Apple Mail) über `k-…`-Klassen und `<style>`; alle anderen zeigen weiter das helle Schema
  aus Inline-Styles. Getestet: Server-Tests (u. a. `test_report.py`, `test_i18n.py`); Vorschau des Wochenberichts aus den
  Entwicklungsdaten auf 375 px in Hell und Dunkel.
- 2026-11-15: Fix Statistik: Der Tooltip der Tagesbalken wurde halb abgeschnitten, weil das seitlich scrollbare Diagramm (`overflow-x:auto`)
  auch oben abschneidet. Diagramm mit 2,6em Platz über den Balken, Tooltips der ersten und letzten drei Balken bündig nach innen.
  Getestet: im Browser (1280 px) Maus über mittlerem, erstem und letztem Balken – Tooltip jeweils vollständig sichtbar.
- 2026-11-15: Anforderungen Multi-Tenant/SaaS per /grill-me abgestimmt (20 Entscheidungen, Abschnitt „Multi-Tenant / SaaS“):
  Mandant = Familie, eine Codebasis mit `KIDCTL_MODE=single|saas`, PostgreSQL + Alembic, `family_id` mit Pflicht-Filterung,
  Feldverschlüsselung je Familie mit Kontextbindung, E-Mail-Anmeldung, Rollen Inhaber/Elternteil, Registrierung mit Schalter,
  Betreiber nur mit Metadaten und Support-Freigabe, Abo-Vorbereitung mit Schonfrist und Ruhemodus, offener Betrieb gegen Missbrauch,
  Export/Import, feste Aufbewahrungsfristen, Umzugsnachricht im Protokoll, fünf Etappen. Noch nicht umgesetzt.
- 2026-11-15: Etappe 1.1 Multi-Tenant: Server auf PostgreSQL umgestellt. Verbindung nur über `DATABASE_URL` (Entwicklung:
  Podman-Container `kidctl-pg` auf 127.0.0.1:55432); SQLite-Adressen brechen den Start mit Hinweis ab. Migrationen mit Alembic
  (`server/app/alembic`, Ausgangsrevision `0001` = Schema 0.32.7), `init_db()` führt `upgrade head` aus; `app/migrate.py` wird
  zu `app/sqlite_import.py`: `cli import-sqlite <kidctl.db> [--apply]` zieht eine Kopie der SQLite-Datei mit den früheren
  Spaltenmigrationen nach, liest nur vorhandene Spalten, prüft VARCHAR-Längen, kopiert alle Tabellen in einer Transaktion
  (Abbruch bei nicht leerem Ziel) und zieht die ID-Folgen nach. Backup per `pg_dump --format=custom` (`.dump`), geprüft mit
  `pg_restore --list`; Dockerfile installiert `postgresql-client-17` aus dem PGDG-Repo. `docker-compose.yml` mit Dienst `db`
  (postgres:17, `./data/postgres`, internes Netz, Healthcheck), `POSTGRES_PASSWORD` in `.env`. Doku: Umstieg und
  Wiederherstellung in `docs/installation.md`; CLAUDE.md-Regeln zu Datenbank und Migrationen angepasst. Getestet: alle
  Server-Tests gegen PostgreSQL (Testdatenbank `kidctl_test`, Leeren per `TRUNCATE`), neuer Test für die Übernahme (Probelauf,
  fehlende Spalten, ID-Folgen, Abbruch bei vollem Ziel), Backup-Tests mit `pg_dump`/`pg_restore` im Container, `alembic check`
  ohne Abweichung; Übernahme der Entwicklungsdaten (317 Zeilen) und WebUI darauf geprüft; Server-Image mit `pg_dump` gebaut.
- 2026-11-15: Etappe 1.2/1.3 Multi-Tenant: Familien und Pflicht-Filterung. Neue Tabellen `family` und `membership` (Rolle owner/parent,
  vorerst eine Mitgliedschaft je Account), `family_id` an 19 familienbezogenen Tabellen (Mixin `FamilyScoped`), Kindernamen nur je
  Familie eindeutig; Alembic-Revision `0002` ordnet bestehende Daten Familie 1 zu, ältester Account wird Inhaber (ebenso
  `import-sqlite`). `app/tenant.py`: jede Datenbanksitzung hat den Kontext Familie oder Plattform; Abfragen auf Familientabellen,
  Accounts, Sitzungen, Tokens und Push-Abos werden automatisch gefiltert (`with_loader_criteria`), ohne Kontext gibt es `TenantError`;
  neue Zeilen bekommen die Familie aus dem Kontext, Schreiben in eine fremde Familie wird abgelehnt. Kontext: angemeldete Eltern nach
  der Sitzungsprüfung, Geräte nach der Token-Prüfung (Kopplungscode bestimmt die Familie des neuen Geräts), anonyme Abläufe und
  Hintergrundaufgaben ausdrücklich als Plattform bzw. je Familie. Push geht nur noch an die Eltern der betroffenen Familie (vorher an alle
  Abos); Betriebshinweise (Backup) nur bei genau einer Familie an Eltern, sonst ins Log. Eltern anlegen/entfernen nur durch den Inhaber,
  Inhaber nicht löschbar, Rolle auf der Elternseite sichtbar. Getestet: alle Server-Tests im Familienkontext; neu `test_tenant.py`:
  Listen, alle Seiten (404) und alle Aktionen (404, nichts geändert) mit IDs einer zweiten Familie, Zuordnung zu fremdem Kind,
  Kopplungscode einer anderen Familie, Abfrage ohne Kontext, Schreiben in fremde Familie, gleicher Kindername in zwei Familien,
  Push-Trennung, Rollen; Migration 0002 auf den Entwicklungsdaten mit `alembic check` ohne Abweichung.
- 2026-11-16: Anforderungen Projekthomepage per /grill-me abgestimmt (Abschnitt „Projekthomepage“). Noch keine
  Umsetzung. README (DE/EN) nennt Windows 10/11 noch nicht als Plattform – wird mit Etappe 1 nachgezogen.
- 2026-11-16: Etappe 1.4 Multi-Tenant: Anmeldung per E-Mail und Betriebsart. `KIDCTL_MODE=single|saas` (Standard `single`, ungültige
  Werte brechen den Start ab). E-Mail-Adressen systemweit eindeutig ohne Groß/klein (Alembic `0003`, partieller Unique-Index auf
  `lower(email)`; Abbruch mit Liste bei vorhandenen Dubletten); neue Spalte `email_verified_at`, bestehende Adressen gelten als
  bestätigt. Neue/geänderte Adresse: mit SMTP Bestätigungslink (48 h, `/email/verify/<token>`, einmalig, „erneut senden“ im Profil);
  ohne SMTP sofort bestätigt. Erst bestätigte Adressen dienen zum Anmelden und bekommen Mails (Passwort zurücksetzen, Hinweise,
  Nutzungsbericht). Login-Feld „E-Mail-Adresse“ (Einzelbetrieb: „… oder Benutzername“), der Benutzername funktioniert weiter. Im SaaS ist die E-Mail
  Pflicht, das Benutzernamen-Feld entfällt (aus der Adresse abgeleitet, eindeutig); `cli create-parent` nimmt
  `KIDCTL_BOOTSTRAP_EMAIL` (im SaaS Pflicht) und legt die erste Familie mit dem Account als Inhaber an. Getestet: alle Server-Tests;
  neu `test_email_login.py` (Bestätigung vor Login, Link einmalig, Groß/klein, Benutzername weiter, Eindeutigkeit über Familien,
  ohne SMTP sofort bestätigt, SaaS: Pflicht und abgeleiteter Benutzername, `KIDCTL_MODE`); Migration 0003 auf den
  Entwicklungsdaten mit `alembic check` ohne Abweichung; Login und Profil im Browser.
- 2026-11-16: Etappe 1.5 Multi-Tenant: Probelauf der Übernahme mit einer Kopie der echten srv02-Datenbank (SQLite inkl. WAL) in eine
  eigene Probedatenbank. Gefunden und behoben: (1) Nachziehen der ID-Folgen scheiterte an `blocklist_state` (Text-Schlüssel, keine
  Folge) – die Übernahme lief vollständig zurück (eine Transaktion), jetzt nur noch für Integer-Schlüssel; (2) die Übernahme schreibt
  direkt ins neueste Schema und umging Migration 0003, vorhandene E-Mail-Adressen wären unbestätigt gewesen – jetzt wie 0003 als
  bestätigt übernommen, doppelte Adressen brechen mit Liste ab. Ergebnis: 579 Zeilen, Familie 1 mit dem bisherigen Account als
  Inhaber, alle Familientabellen mit `family_id` 1, ID-Folgen korrekt, `alembic check` ohne Abweichung; WebUI gegen die Probedatenbank
  (ohne Push/Mail): Übersicht, alle Reiter des Kindes, Geräte (Linux 0.26.2, Windows 0.32.7), Eltern, Einstellungen, Profil ohne
  Fehler, 27 Chatnachrichten, 17 freigegebene Programme, 316 Protokolleinträge. Probedaten danach gelöscht. Getestet zusätzlich:
  `test_migrate.py` um Text-Schlüssel, bestätigte und doppelte E-Mail-Adressen erweitert; alle Server-Tests.
- 2026-11-16: Projekthomepage Etappe 1: README/README.de nennen Windows 10 (22H2) und 11 (ab 23H2), x64, Home und Pro
  in Einleitung, Funktionen und Architektur, Installations-Kurzfassung mit Windows-Installer, Grenzen um die
  Windows-Einschränkungen ergänzt. `kidctl-release.sh` legt als Schritt 5 das GitHub-Release `v<Version>` an
  (`gh release create --verify-tag`, Installer als Asset, SHA-256, Link zum Lauf, Kurztext aus `--notes DATEI`,
  `packaging/repo/notes/v<Version>.md` oder den ersten Sätzen der Changelog-Einträge mit der Version); vorhandenes
  Release wird übersprungen, ohne `--apply` wird der Text nur angezeigt. Getestet: Probelauf mit Lauf 42863441320
  (0.32.7, Publish-Ziel lokal umgebogen) – Befehl und Text korrekt, Hinweis auf bestehendes Release; ein echtes
  Release erst beim nächsten Versionssprung.
- 2026-11-16: Projekthomepage Etappe 2 (Gerüst): `website/` mit Hugo 0.170 (Englisch unter `/`, Deutsch unter `/de/`,
  `baseURL` GitHub Pages), eigenes Theme `themes/kidctl` im Material-3-Stil (Farbrollen der WebUI, Systemschriften,
  Inline-SVG-Symbole, hell/dunkel über `prefers-color-scheme`, JS nur für die Screenshot-Lightbox). Seiten: Startseite
  (Hero, Plattformen, Funktionen mit Screenshot-Platzhaltern, So funktioniert's, Datenschutz & Sicherheit,
  Schnellstart, Roadmap, letzte News), News mit RSS je Sprache, Releases aus `data/releases.json`, Impressum mit
  `TODO-IMPRESSUM`, Datenschutz (GitHub-Pages-Logs). Inhalte in `data/*.yaml` (Felder `de`/`en`), `roadmap.yaml` mit
  erstem Stand inkl. SaaS. Workflow `pages.yml` (Hugo 0.170.0 gepinnt, mit SHA-256-Prüfung; Releases per `gh api` →
  Platzhalter-Check → `hugo --minify --panicOnWarning` → Deploy). Getestet: `hugo --minify --gc --panicOnWarning`
  ohne Warnungen; Playwright-Durchlauf über Start-, News-, Release-, Impressum- und Datenschutzseite in 390×844 und
  1280×800, hell/dunkel, DE/EN: kein horizontaler Überlauf, keine externen Requests, keine Konsolenfehler.
  Pages-Workflow selbst noch nicht gelaufen (erst nach Merge auf main).
- 2026-11-16: Projekthomepage Etappe 3 (Screenshots): `dev/demo_seed.py` legt fiktive Demo-Daten nur über echte
  Schnittstellen an – Eltern-Aktionen als HTTP-Formulare, Rechner als simulierte Agenten (Kopplung + WebSocket,
  bleiben verbunden: „online“, „spielt gerade“); nur Nutzung älter als 7 Tage direkt in SQLite. Inhalt: Lena und Max,
  Linux-Laptop und Windows-Familien-PC, 14 Tage Nutzung mit Spielen, Programme, Website-Filter, gesperrte Aktionen,
  Chat, eine genehmigte und drei offene Anfragen; Texte je Sprache. `dev/screenshots.py` startet je Sprache einen
  frischen Server (Temp-Verzeichnis, Port 8010), seedet, meldet sich per Session an und fotografiert mit Playwright
  9 Seiten (Übersicht, Kind Heute/Zeiten/Programme/Internet, Statistik, Chat, Protokoll, Geräte; Liste mit Adrian
  abgestimmt) mobil 390×844 und Desktop 1280×800 × hell/dunkel × DE/EN → 72 WebP (1,5-fach, zusammen 2,6 MB).
  Startseite: Handy-Bild der Übersicht im Hero (sofort geladen), Funktionen mit Bild passend zum Farbschema,
  Client-Screenshots (`static/img/client/`) als Platzhalter bis zur Lieferung aus der VM. Getestet: kompletter Lauf
  DE+EN, Sichtprüfung der Bilder, `hugo --minify --panicOnWarning` ohne Warnungen, Playwright-Check der Seite ohne
  Überlauf und ohne externe Requests.
- 2026-11-16: Projekthomepage Etappe 4 (Inhalte): Funktionstexte, Schnellstart und Datenschutz DE/EN (in Etappe 2
  angelegt, geprüft); sieben News nach Freigabe durch Adrian: erster vollständiger Stand (0.8.0), Spiele-Limit
  (0.25.0), GNOME/Debian/Ubuntu/Englisch (0.28.0), Windows (0.32.0), Open Source unter AGPL-3.0, neue WebUI im
  Material-3-Stil, Projektseite online. Eigenes RSS-Template (Titel „KidCTL – News“, Zusammenfassung aus `summary`),
  Link „Release auf GitHub“ nur, wenn es das Release gibt; Roadmap „Projektseite“ auf `done`. Getestet:
  `hugo --minify --gc --panicOnWarning` ohne Warnungen, RSS je Sprache mit 7 Einträgen, Playwright-Check (Start,
  News-Liste, News-Seite, Releases, Datenschutz, Impressum; mobil/Desktop, hell/dunkel, DE/EN) ohne Überlauf und ohne
  externe Requests. Offen bis zum Livegang (Adrian): Domain, Impressum-Adresse (`TODO-IMPRESSUM` blockiert den
  Deploy), GitHub Pages auf „GitHub Actions“ stellen, Client-Screenshots aus der VM nach `website/static/img/client/`.
- 2026-11-16: Screenshots auf PostgreSQL umgestellt (nach Rebase auf main mit Etappe 1.x Multi-Tenant):
  `dev/screenshots.py` legt je Sprache eine frische Datenbank `kidctl_shots` im Container `kidctl-pg` an
  (`KIDCTL_MODE=single`) und löscht sie danach; `dev/demo_seed.py` nimmt `--database-url` (verweigert die
  Entwicklungsdatenbank) und setzt bei den älteren Nutzungszeiten `family_id` vom Gerät. Getestet: Lauf DE für
  Übersicht, Statistik, Chat, Geräte – optisch gleich den committeten Bildern, Datenbank danach entfernt,
  Entwicklungsdatenbank unberührt.
- 2026-11-16: Fix Projektseite: `website/data/*.yaml` (Funktionen, Plattformen, Schnellstart, Datenschutz-Punkte,
  Roadmap) fehlten im Repo, weil das Wurzel-`.gitignore` jedes `data/` ausschließt – der erste Pages-Lauf scheiterte
  beim Schreiben von `website/data/releases.json`. `website/.gitignore` nimmt `data/` wieder auf (`releases.json`
  bleibt ignoriert). Getestet: sauberer Export von HEAD (`git archive`) + Releases per `gh api` + `hugo --minify
  --panicOnWarning` ohne Warnungen, Startseite enthält Funktionen und Roadmap.
- 2026-11-16: srv02 auf PostgreSQL umgestellt (Etappe 1 Multi-Tenant live): Übernahme mit `import-sqlite` (579 Zeilen, wie im Probelauf),
  Server läuft, Anmeldeseite mit „E-Mail-Adresse oder Benutzername“ von außen geprüft. Anleitung „Umstieg auf PostgreSQL“ in
  `docs/installation.md` korrigiert: vor der Übernahme `docker compose build kidctl` (sonst läuft der Befehl im alten Image ohne
  PostgreSQL-Treiber und bricht mit `No module named 'psycopg'` ab, ohne etwas zu ändern).
- 2026-11-16: Roadmap der Projektseite: „Gehostete Variante für Familien“ auf „in Arbeit“ (SaaS-Etappe 1 abgeschlossen). Getestet:
  `hugo --minify` ohne Fehler.
- 2026-11-16: Impressum: Anschrift „folgt“ (bis Adrian sie festlegt), Kontakt `info@nossco.de`; damit besteht der
  Platzhalter-Check im Pages-Workflow. Prüfsumme 0.32.7 geklärt: Das GitHub-Release enthält den CI-Build aus Lauf
  42863441320 (2026-11-10, `0e9a…`, identisch mit dem Artefakt), im lokalen bzw. veröffentlichten Repo liegt noch der
  VM-Build vom 2026-11-08 (`d8e2…`, vor Einführung des CI-Baus, von `latest.json` signiert). Kein Fehler, ab dem
  nächsten Release kommen beide aus demselben CI-Lauf. Getestet: `hugo --minify --panicOnWarning`, Prüfsummen von
  Release-Asset und CI-Artefakt verglichen.
- 2026-11-16: Etappe 2.1 Multi-Tenant: Verschlüsselungskern (`app/crypto.py`). Hauptschlüssel `KIDCTL_MASTER_KEY` (32 Byte Base64,
  außerhalb der Entwicklung Pflicht, `cli master-key` erzeugt ihn), daraus per HKDF Schlüssel zum Einpacken und für Suchwerte.
  Jede Familie bekommt beim Anlegen einen eigenen 64-Byte-Datenschlüssel, eingepackt in `family.wrapped_key` (Alembic `0004`
  erzeugt ihn für bestehende Familien). Spaltentypen `Sealed` (AES-256-GCM, zufällig) und `Matchable` (AES-256-SIV,
  deterministisch für Gleichheit/Eindeutigkeit/Gruppieren), Prüfdaten Familie + Tabelle.Spalte; `tenant.py` setzt den Schlüssel-
  kontext bei jeder Abfrage und jedem Speichern auf die Familie der Sitzung (Plattform/kein Kontext = kein Schlüssel). Entscheidung
  2026-11-16: Programm-/Website-Schlüssel deterministisch verschlüsseln. Noch keine Spalte umgestellt. Getestet: alle Server-Tests,
  neu `test_crypto.py` (eigener Schlüssel je Familie, Bindung an Familie/Spalte, deterministisch vs. zufällig, ohne Kontext Fehler,
  E-Mail-Suchwert, Hauptschlüssel Pflicht); Migration 0004 auf den Entwicklungsdaten, `alembic check` ohne Abweichung.
- 2026-11-16: Etappe 2.2 Multi-Tenant: sensible Felder verschlüsselt (Alembic `0005` stellt auf TEXT um und verschlüsselt vorhandene
  Werte mit dem Schlüssel der Familie der Zeile). Zufällig (`Sealed`): Chat-Text, Namen von Programmen/Websites/Spielen in Protokoll und
  Statistik, Details im Protokoll, Gerätename und Hostname, voller Name der Benutzer auf Geräten. Deterministisch (`Matchable`):
  Kindername, Benutzernamen auf Geräten (Zuordnung, Nutzungszeiten, Protokoll, Statistik, Spielsitzungen), Programm-/Website-Schlüssel in
  Protokoll, Statistik, Spielsitzungen und Freigabe-Anfragen. Katalog, Freigaben und eigene Domain-Regeln bleiben unverschlüsselt.
  Folgen: Freitextsuche im Protokoll und Namen in Statistik/Bericht/„Häufigste“ im Server statt per SQL; Listen von Kindern und Geräten
  im Server sortiert; Alarme und Sperrlisten-Push laufen je Familie; Token-Prüfung im Plattformkontext liest nur unverschlüsselte
  Spalten; `import-sqlite` liest roh, verschlüsselt mit Familie 1 und legt deren Schlüssel selbst an. Getestet: alle Server-Tests (u. a.
  Übernahme mit Schlüssel aus der Datenbank, gleicher Kindername in zwei Familien mit verschiedenem Geheimtext, Plattform liest keine
  Namen); Migration 0005 auf den Entwicklungsdaten (`alembic check` ohne Abweichung, Datenbank enthält nur Geheimtext); WebUI auf den
  verschlüsselten Daten: alle Seiten ohne Fehler und ohne Geheimtext, Sortierung, Protokollsuche.
- 2026-11-16: Etappe 2.3 Multi-Tenant: E-Mail-Adressen der Eltern verschlüsselt (`Sealed`, Schlüssel der Familie des Accounts);
  Anmeldung und Eindeutigkeit über den Suchwert `parent.email_index` = HMAC(abgeleiteter Hauptschlüssel, lower(E-Mail)), eindeutiger
  Teilindex ersetzt `lower(email)` (Alembic `0006` verschlüsselt vorhandene Adressen). Ein Ereignis hält den Suchwert bei jeder Änderung
  aktuell. Anonyme Abläufe (Login, Passwort zurücksetzen, Bestätigungs- und Authenticator-Links) suchen nur über unverschlüsselte
  Spalten und wechseln vor dem Laden in den Kontext der Familie des Accounts (`tenant.load_parent`); Nutzungsbericht je Empfänger im
  Familienkontext; `cli create-parent` legt erst die Familie an. Getestet: alle Server-Tests, neu: E-Mail nur als Geheimtext in der
  Datenbank, Anmeldung über Suchwert ohne Groß/klein; Migration 0006 auf den Entwicklungsdaten, `alembic check` ohne Abweichung.
- 2026-11-16: Etappe 2.4 Multi-Tenant: Familie löschen per Schlüssel (`tenant.delete_family`, `cli delete-family <id> [--apply]`, ohne
  `--apply` nur Anzeige): zuerst wird der Datenschlüssel verworfen und gespeichert (Daten sofort unlesbar, auch in älteren Backups), dann
  Accounts samt Sitzungen/Tokens/Push-Abos und alle Familiendaten gelöscht (CASCADE). Bedienung im WebUI folgt mit Etappe 4/5.
  Getestet: alle Server-Tests, neu: Geheimtext aus „altem Backup“ nach dem Löschen nicht mehr zu öffnen, alles der Familie entfernt,
  andere Familie unberührt.
- 2026-11-16: Etappe 2.5 Multi-Tenant: Aufbewahrungsfristen (`app/retention.py`, alle 6 Stunden im Plattformkontext, nur nach
  Zeitstempeln): Protokoll, Spielsitzungen, Programmnutzung 90 Tage; Bildschirmzeit je Tag 395 Tage; Chat 365 Tage; Anmeldeversuche 30
  Tage; abgelaufene Sitzungen/Links 30 Tage nach Ablauf; je Datenart per `KIDCTL_KEEP_<NAME>_DAYS` änderbar (ersetzt das bisherige
  Aufräumen des Protokolls). „Chat leeren“ je Kind (Knopf auf der Chatseite mit Rückfrage). Doku: Aufbewahrung, Familie löschen, Update-
  Hinweis zum Hauptschlüssel. Getestet: alle Server-Tests, neu `test_retention.py` (Fristen je Datenart, Grenze 13 Monate, Änderung per
  Umgebung, Chat leeren nur für das gewählte Kind). Protokoll der Support-Zugriffe (2 Jahre) folgt mit Etappe 4.
- 2026-11-17: Anforderungen Wiki per /grill-me abgestimmt (Abschnitt „Projekthomepage“ → „Wiki“): `docs/` als
  Wiki auf der Projektseite, DE/EN mit Paritäts-Check, Aufteilung in Themenseiten, Pagefind-Suche. Noch keine
  Umsetzung.
- 2026-11-17: Anforderungen Sicherheitsrichtlinie per /grill-me abgestimmt (Abschnitt „Sicherheitsrichtlinie“):
  `SECURITY.md` DE/EN, vertraulicher Meldeweg, Restlücken nach `docs/limitations*.md`, `security.txt` in Server
  und Projektseite, Dependabot/CodeQL/Secret Scanning/pip-audit. Noch keine Umsetzung.
- 2026-11-17: Etappe 2 Probelauf mit einem `pg_dump` der echten srv02-Datenbank (Stand Revision 0003) und neu erzeugtem Hauptschlüssel ohne
  Entwicklungsmodus: Migrationen 0004–0006 in unter 1 s, alle 416 verschlüsselten Zeilen in neuem Prozess (Schlüssel aus der Datenbank)
  entschlüsselbar, alle 16 WebUI-Seiten ohne Fehler und ohne Geheimtext (316 Protokolleinträge, 27 Chatnachrichten). Abgleich aller
  entschlüsselten Werte mit dem rohen Dump fand zwei Lücken, beide behoben: Gerätename der Kopplungscodes (Alembic `0007`, Kopplung
  liest im Plattformkontext nur unverschlüsselte Spalten) und Ergebnis der Integritätsprüfung mit Benutzernamen/Pfaden der Kinder
  (Alembic `0008`); abgelaufene Kopplungscodes werden nach 1 Tag gelöscht (`KIDCTL_KEEP_PAIRING_DAYS`). Danach im Klartext nur noch
  bewusst unverschlüsselte Bereiche: Programmkatalog, Freigaben, Eltern-Benutzername/Anzeigename, Anmeldeversuche, Browserkennung der
  Sitzung, technischer Sperrcode. Probedaten gelöscht. Getestet: alle Server-Tests.
- 2026-11-17: Wiki Etappe 1 (Umbau Deutsch): `docs/installation.md` → `installation.de.md` (git mv, jetzt Übersicht:
  Reihenfolge, Voraussetzungen, Härtung, Betrieb) plus Themenseiten `server`, `package-repo`, `child-linux`,
  `child-windows`, `webui` (mit „Sprache“), `games`, `updates`, `postgresql-migration`, `backup`, `uninstall` (je
  `.de.md`); `haertung.md` → `hardening.de.md` (git mv); `code-signing.md` geteilt in EN (`code-signing.md`) und DE
  (`code-signing.de.md`, bisheriger Abschnitt „Deutsch“). Bedien-Teile aus `packaging/repo/README.md` (Schlüssel-
  Details, Release-Schritte, Client-Skript) und `packaging/windows/README.md` (Was der Installer einrichtet, Updates,
  Deinstallation) nach `docs/package-repo`, `child-linux` und `child-windows`; die Komponenten-READMEs behalten
  Interna und verweisen auf die Anleitung. Abschnittsnummern entfallen, Querverweise („siehe 1.4“, „(2.2)“, „unten“)
  sind jetzt Links; Hinweise als GitHub-Alerts (Datenordner sichern, privaten Schlüssel sichern, Admins nie
  eingeschränkt, SmartScreen). Links in README, `server/`, `packaging/arch` nachgezogen. Getestet: Skript prüft alle
  relativen Links und Anker in allen `.md` des Repos (26 Dateien, 0 Fehler); Abgleich jeder Zeile der alten Dateien
  gegen den neuen Bestand – fehlend nur umnummerierte Überschriften, umformulierte Querverweise/Alerts und
  zusammengeführte Doppelungen (z. B. Kopplungsbefehl, Client-Skript mit `kids.nossco.de`).
- 2026-11-17: Wiki Etappe 2 (Englisch + Paritäts-Check): englische Fassungen aller `docs/`-Seiten (`installation.md`
  wieder unter dem alten Pfad, jetzt Englisch), `code-signing.de.md` als vollständige Übersetzung, README.md als
  Übersetzung von README.de.md (Basis Deutsch; im Deutschen ergänzt, was nur im Englischen stand: Spiele-Limit,
  Freigabe-Anfragen, Heroic, Nutzungsbericht, Sprachen, Übersetzungsdateien). Sprachneutral gemacht, damit Codeblöcke
  identisch sind: Platzhalter `<FINGERPRINT>`, `<PAIRING-CODE>`, `<child>`, `<user>`, GRUB-Benutzer `parents`,
  Meldungen im GRUB-Skript/Pacman-Hook, Sicherungsdatei `kidctl.db.bak`, Architekturdiagramm im README.
  `dev/check_docs_parity.py` (Paare docs/README/SECURITY, Überschriftenfolge, Codeblöcke ohne Kommentare, Tabellen-
  zeilen, Listenpunkte, Hinweisboxen je Typ, Linkziele ohne Anker/Sprachendung, Bilder ohne Sprachordner,
  `website/data/docs.yaml` ↔ `docs/`), Ausgabe `<datei>:<zeile>: …`; `website/data/docs.yaml` mit den Gruppen
  Erste Schritte, Betrieb, Sicherheit. Getestet: Check grün (14 Paare); `dev/tests/test_check_docs_parity.py`
  (9 Tests) mit Gegenproben – Überschrift nur im Deutschen, Befehl nur im Englischen geändert, Listenpunkt nur im
  Englischen, Linkziel, Hinweisbox-Typ, fehlende Übersetzung, Navigation ohne Datei – jeweils Fehler mit Datei und
  Zeile, abweichende Kommentare kein Fehler; Linkprüfung aller `.md` (38 Dateien, 0 Fehler).
- 2026-11-17: Wiki Etappe 3 (Wiki im Theme): Hugo-Mounts `../docs` → `content/docs` (ohne `img/`) und `../docs/img` →
  `static/docs/img`; Abschnitt „Anleitungen“/„Docs“ (`/docs/`, `/de/docs/`) mit Übersichtsseite nach Gruppen aus
  `data/docs.yaml`. Seitenlayout: Navigation links (mobil als aufklappbares Menü), Inhaltsverzeichnis rechts (H2/H3,
  mobil aufklappbar), Vor/Zurück in der Reihenfolge aus `docs.yaml`, „Auf GitHub bearbeiten“ auf die Quelldatei der
  Sprache, Kopier-Button an Codeblöcken (`docs.js`, ohne JS normal markierbar). Titel aus der ersten `#`-Überschrift
  (Partial `pagetitle`, im Text ausgeblendet). Render-Hooks: Links in `docs/` → Wiki-Seite derselben Sprache (mit
  Anker), übrige Repo-Pfade → GitHub `blob/main`; Bilder aus `docs/img/`; GitHub-Alerts als Hinweisboxen.
  Menüpunkt „Anleitungen“/„Docs“ (ersetzt „Installieren“ im Kopf; Schnellstart bleibt per Hero-Button), Buttons
  „Vollständige Anleitung“ und „Härtung“ im Schnellstart (`params.docs` = Wiki; vorher Link auf das entfallene
  `haertung.md` bei GitHub). Pages-Workflow: Paritäts-Check vor dem Build, Auslöser auch `docs/**`, `SECURITY*.md`.
  Getestet: `hugo --minify --gc --panicOnWarning` ohne Warnungen; jede Wiki-Seite DE/EN (28) mobil 390×844 und
  Desktop 1366×900, hell/dunkel per Playwright: kein Überlauf, Titel gesetzt, keine externen Requests, keine
  Konsolenfehler/404; Linkprüfung über `public/` (2166 interne Links inkl. Anker, 0 Fehler; alle 40 GitHub-Ziele
  existieren im Repo); Sichtprüfung Übersicht, Server (mobil dunkel), Windows (Desktop hell). GitHub-Ansicht der
  Markdown-Dateien unverändert lesbar (reines Markdown, Alerts nativ).
- 2026-11-17: Wiki Etappe 4 (Suche): Pagefind 1.6.0 (pip `pagefind[bin]`, im Pages-Workflow gepinnt, nach `hugo`).
  Indexiert werden Startseite, Wiki (`data-pagefind-body`, Filter `type` = „Anleitungen“/„Docs“) und News (Filter
  „News“); Sprachen getrennt über `<html lang>`. Eigener Suchdialog im Material-Stil (`search.js`, Pagefind-JS-API,
  `baseUrl` = Unterpfad der Seite): Lupe im Kopf, `Strg+K`/`⌘K` und `/`, Filter-Chips Alle/Anleitungen/News,
  Pfeiltasten in den Treffern, Index wird erst beim Fokus/Tippen geladen. Ohne JavaScript führt die Lupe auf
  `/search/` mit Hinweis und Links zu Anleitungen und News. Getestet (Playwright gegen `hugo` + `pagefind` unter
  `/kidCTL/`): kein Pagefind-Request vor der Suche; EN „installing“ findet „Installation“, DE „Kinderrechners“ findet
  „Kinderrechner“ (Stemming); „Kinderrechner“ auf einer englischen Seite ohne Treffer (Sprachen getrennt); Filter
  News/Anleitungen liefern nur den jeweiligen Typ; Treffer-Link landet unter `/kidCTL/`; keine externen Requests,
  keine Konsolenfehler; ohne JS erreichbare Suchseite; Sichtprüfung Dialog dunkel/Desktop und hell/Handy.
- 2026-11-17: Wiki Etappe 6 (Verweise): README DE/EN verlinken im Abschnitt Installation ins Wiki („Anleitungen:
  Wiki (Quelle: docs/)“, Installationsanleitung, Windows, Härtung); Release-Text in `kidctl-release.sh` verlinkt
  Linux-, Server- und Installationsanleitung sowie Code-Signatur/Datenschutz im Wiki (EN); `repo.html` verlinkt die
  Code-Signatur im Wiki in der Sprache des Eltern-Accounts; Fehlermeldung in `config.py` (SQLite) nennt Wiki-URL und
  `docs/postgresql-migration.md`; Datenschutzseite der Projektseite und `server/README.md` zeigen aufs Wiki. Paritäts-
  Check behandelt Wiki-URLs beider Sprachen als gleiches Ziel. Weitere `docs/`-Verweise im Repo gesucht (Komponenten-
  READMEs zeigen relativ auf `docs/*.de.md`, bleiben so). Getestet: Dry-Run `kidctl-release.sh` mit Lauf 42863441320
  (Text mit Wiki-Links), Server-Tests 160 grün (eigene Testdatenbank `kidctl_test_homepage`, weil `kidctl_test` schon
  Revision 0008 aus dem SaaS-Branch trägt), Paritäts-Check und Linkprüfung grün.
- 2026-11-17: Wiki Etappe 8 (Restlücken öffentlich): Abschnitt „Bewusst nicht umgesetzt / Restlücken“ nach
  `docs/limitations.de.md`/`limitations.md` („Grenzen und bekannte Lücken“, für Eltern formuliert, gegliedert nach
  Überall/Linux/Windows, Wiki-Gruppe Sicherheit) – dort einzige Quelle; die Spec verweist nur noch. Interne
  Begründungen als „Restlücken – Hintergrund“ in die Fachabschnitte Programm-Whitelist, Website-Filter und
  Architektur → Windows (Named Pipe/`GENERIC_WRITE`, Credential Provider, feste Listen in `win/dnsfilter.py`,
  `dnscache` 30 s, Prozessliste 0,5 s, `git` nicht in der Basisliste, `flush ruleset` …). README-Kurzliste verlinkt
  die Seite; Verweise auf `docs/haertung.md` in der Spec auf `hardening.de.md`. Getestet: Abgleich aller 34
  Restlücken der alten Liste gegen `limitations.de.md` per Stichwort (0 fehlend), Paritäts-Check (15 Paare),
  Linkprüfung Repo (42 Dateien) und Seite (2502 Links), `hugo --minify --panicOnWarning` ohne Warnungen.
- 2026-11-17: Sicherheitsrichtlinie Etappe 7: `SECURITY.de.md` und `SECURITY.md` im Repo-Root nach Spec (Meldeweg
  Private Vulnerability Reporting bzw. security@nossco.de, kein PGP, nie öffentlich; nur neueste Version; Rahmen und
  Ausschlüsse mit Links auf Härtung und Grenzen; Fristen 7/14/30/90 Tage; Advisory, CVE ab mittlerer Schwere, kein
  Bug-Bounty; Testregeln; Safe Harbor – Formulierung noch bei der Anwaltsprüfung prüfen). Paritäts-Check (16 Paare);
  per Einzeldatei-Mount Wiki-Seite `security` (Gruppe Sicherheit, „Bearbeiten“ auf die Datei im Root, Links auf
  `docs/` werden zu Wiki-Links). „Reporting“/„Meldungen“ in `docs/code-signing*` verweist auf die Richtlinie statt auf
  Issues; README DE/EN mit Abschnitt „Sicherheit“/„Security“; `docs/limitations*` verlinkt die Richtlinie. Getestet:
  Paritäts-Check und pytest grün, Linkprüfung Repo (44 Dateien) und Seite (2728 Links) ohne Fehler, Wiki-Seite DE
  mit umgeschriebenen Links (Härtung, Grenzen) und Bearbeiten-Link auf `SECURITY.de.md`.
- 2026-11-17: Sicherheitsrichtlinie Etappe 10 (GitHub): `.github/dependabot.yml` – wöchentlich (montags), je
  Ökosystem ein gruppierter PR für `pip` (`/server`) und `github-actions` (`/`). `.github/workflows/audit.yml` –
  montags, von Hand und bei Änderung von `vendor.lock`: `pip-audit` 2.10.1 (venv) gegen die Wheels aus
  `packaging/windows/vendor.lock` (`--no-deps --disable-pip --strict`), Fund → Fehlschlag; `actions/checkout` per
  Commit-SHA gepinnt. Python (embeddable) und Unbound deckt das nicht ab (Hinweis im Workflow). Getestet: beide
  YAML-Dateien gültig; pip-audit lokal mit derselben Befehlszeile – „No known vulnerabilities found“, Exit 0;
  Gegenprobe mit `websockets==9.0` meldet PYSEC-2021-95, Exit 1.
- 2026-11-17: Sicherheitsrichtlinie Etappe 9 (security.txt, RFC 9116): Server-Route `/.well-known/security.txt` ohne
  Anmeldung (`app/routes/wellknown.py`, eigener Router, `config.py` unverändert): optional zuerst
  `SECURITY_CONTACT` des Betreibers (E-Mail → `mailto:`, oder https-URL), dann `mailto:security@nossco.de` und
  GitHub-Meldeweg, `Expires` = jetzt + 365 Tage, `Policy` = Wiki-Seite der Richtlinie, `Preferred-Languages: en, de`,
  `Canonical` aus `BASE_URL`. `.env.example` und `docs/server*` (Tabelle + Testhinweis) ergänzt. Projektseite:
  `website/static/.well-known/security.txt`, `Expires` setzt der Pages-Build auf heute + 1 Jahr (wirksam erst mit
  eigener Domain). Server-Version noch nicht erhöht (Rückfrage: es gibt nur die gemeinsame Paketversion). Getestet:
  `server/tests/test_security_txt.py` (ohne Login 200 text/plain, Felder, Expires ≈ 1 Jahr, Betreiberkontakt zuerst
  als E-Mail/URL, ungültiger Wert ignoriert), Server-Tests gesamt 163 grün; Hugo-Build übernimmt `.well-known/`,
  `sed`-Schritt des Workflows lokal geprüft; Paritäts-Check und Linkprüfung grün.
- 2026-11-17: Wiki: Änderungen der SaaS-Etappe 2 an `docs/installation.md` nach dem Rebase in die Themenseiten
  übernommen (DE + EN): `KIDCTL_MASTER_KEY` in der `.env`-Tabelle von `server`, Zeile „Verschlüsselung je Familie
  (einmalig)“ in `updates`, Abschnitt „Aufbewahrung“ als eigene Seite `retention` („Aufbewahrung“/„Data retention“,
  Gruppe Betrieb, inkl. „Chat leeren“ und `cli delete-family`). Getestet: Paritäts-Check (17 Paare), pytest
  `dev/tests`, Linkprüfung Repo und Seite, `hugo --minify --panicOnWarning` ohne Warnungen.
- 2026-11-17: Wiki Etappe 5 (Screenshots in der Doku): `dev/screenshots.py` erzeugt zusätzlich sieben Anleitungsmotive
  (hell, Desktop 1280×800, 1,5-fach WebP) nach `docs/img/<de|en>/` – Motive mit Adrian abgestimmt: `pair`
  (Kopplungscode), `children` (Kind anlegen), `device-mapping` (Benutzer-Zuordnung), `programs`, `web-filter`,
  `game-limit` (Zeiten mit „davon Spiele“), `access-request` (Chat mit Freigabe- und Spielzeit-Anfrage); eingebunden in
  `child-linux` und `webui` (DE/EN, auch auf GitHub sichtbar). Kopplungscode per HTTP erzeugt und die Antwortseite im
  Browser angezeigt (die CSRF-Herkunftsprüfung lässt den Browser-Request von 127.0.0.1 nicht zu). `dev/demo_seed.py`
  an die Verschlüsselung der SaaS-Etappe 2 angepasst (Kind-IDs ohne Namenssuche, Benutzer-Zuordnung aus der
  Geräteseite statt per SQL). Seitenbilder neu erzeugt (Chat zeigt jetzt „Chat leeren“ und die Aufbewahrungsfrist).
  Getestet: kompletter Lauf DE+EN (Demo-Datenbank danach entfernt), Sichtprüfung aller Motive, Paritäts-Check (Bilder
  bis auf den Sprachordner gleich), Linkprüfung Repo und Seite (2936 Links inkl. Bilder), Wiki-Seiten mit Bildern
  Desktop hell/Handy dunkel ohne Überlauf, alle Bilder laden.
- 2026-11-17: Wiki und Sicherheitsrichtlinie als umgesetzt markiert; Roadmap-Eintrag „Anleitungen auf Deutsch und
  Englisch“ (`done`). Getestet: `hugo --minify --panicOnWarning` ohne Warnungen.
- 2026-11-17: Fix Pages-Workflow: `upload-pages-artifact` lässt Dateien mit Punkt am Anfang standardmäßig weg –
  `/.well-known/security.txt` fehlte auf der Live-Seite (404). Jetzt `include-hidden-files: true`. Getestet: nach dem
  Deploy Abruf der Datei auf der Live-Seite.
- 2026-11-17: Etappe 3 Multi-Tenant (Agent 0.33.0): Protokoll ohne neue Version erweitert (ältere Agenten ignorieren unbekannte
  Nachrichten und Felder). **Ruhemodus:** `Policy.paused` + `paused_users`; der Server schickt bei gesetztem `family.paused_at` (Alembic
  `0009`, gesetzt ab Etappe 5) eine Policy ohne Zuordnungen und Kinder – damit heben auch ältere Agenten alle Einschränkungen auf; neue
  zeigen im Tray „KidCTL ist pausiert – keine Einschränkungen“; Zurücksetzen bringt die Regeln sofort zurück. **Server-Umzug:**
  Nachricht `relocate` (nur `https://`), die der Server nach der Policy sendet, solange `KIDCTL_RELOCATE_TO` gesetzt ist; der Agent
  meldet sich per Probeverbindung mit seinem Token beim neuen Server an und übernimmt die Adresse (`device.json`, alte als
  `previous_server_url`) nur nach `welcome`, sonst bleibt er. **Hinweis beim ersten Anmelden:** Die Tray-App zeigt einmal je Benutzer
  (Merker im Profil, nicht blockierend), dass der Account verwaltet wird, was erfasst wird und was nicht. Website: News 0.33, zwei
  Punkte unter „Datenschutz & Sicherheit“; README DE/EN ergänzt. Nebenbei: E2E-Tests des Agenten nutzten seit Etappe 1 die
  Entwicklungsdatenbank (scheiterten an vorhandenem Account) – jetzt eigene, frisch angelegte Datenbank `kidctl_e2e`; erwarteter Text
  der Übersicht an die neue WebUI angepasst. Getestet: Server (171), Agent (274, u. a. Umzug angenommen/abgelehnt/gleiche Adresse,
  Ruhemodus ohne Warnung/Abmeldung, ältere Agenten ignorieren Neues, E2E gegen echten Server), Tray (19); Server über echte
  WebSocket-Verbindung: pausieren → leere Policy, wieder aktiv → Regeln zurück, `relocate` nach der Policy; `hugo --minify` ohne
  Warnungen. Nicht getestet: Umzug zwischen zwei echten Servern, Hinweis-Dialog in einer echten Desktop-Sitzung (VM).
- 2026-11-17: Etappe 3 in der Arch-VM (Plasma, Wayland) mit 0.33.0 aus dem Paketbau gegen den Entwicklungsserver geprüft: Hinweis beim
  ersten Anmelden erscheint nach dem Login von `kind` gut lesbar und nicht blockierend, Merker im Profil, nach Neustart der Tray-App kein
  zweites Mal; Ruhemodus (`family.paused_at` gesetzt, Agent neu verbunden): Policy ohne Zuordnungen, Tray zeigt das Pause-Symbol; nach
  dem Zurücksetzen dieselbe Policy-Version wie vorher und Restzeit wieder im Tray. Server-Umzug in der VM nicht prüfbar (Entwicklungsserver
  nur `http://`, Umzug erlaubt nur `https://`) – durch Unit-Tests abgedeckt. Auf aktuellen main (Wiki) gesetzt; `KIDCTL_RELOCATE_TO` in
  `docs/server(.de).md`; Paritäts-Check grün.
- 2026-11-17: Dependabot: `bcrypt` ab 4.1 ignoriert (passlib 1.7.4 bricht mit bcrypt 5.0.0 schon beim Laden ab:
  „password cannot be longer than 72 bytes“). Dependabot-PR #1 (fastapi 0.163.0, SQLAlchemy 2.1.8, python-multipart 0.0.33,
  pydantic 2.14.3, tzdata 2026.6) lokal geprüft: alle 174 Server-Tests grün mit bcrypt 4.0.1; mit bcrypt 5.0.0 Abbruch bestätigt.
- 2026-11-17: Etappe 4.1 Eltern einladen per Link: Inhaber lädt unter „Eltern“ per E-Mail-Adresse (+ optional Anzeigename) ein;
  neue Tabelle `family_invite` (Migration 0010, nur Hash des Links, E-Mail und Anzeigename verschlüsselt), 7 Tage gültig,
  einmalig, mit SMTP auch per Mail, offene Einladungen sichtbar und zurückziehbar. `/invite/<token>`: Passwort (Einzelbetrieb
  auch Benutzername, SaaS abgeleitet), Account als Elternteil mit bestätigter E-Mail, danach direkt die vorhandene
  Authenticator-Einrichtung (`/totp-setup/…`). Das Anlegen eines Accounts mit Passwort durch den Inhaber entfällt.
  Getestet (pytest): kompletter Ablauf Einladen → Mail → Annehmen → Authenticator → Login per E-Mail; Link nur einmal gültig;
  abgelaufene und zurückgezogene Einladungen 404; fremde Familie sieht die Einladung nicht; nur Inhaber darf einladen;
  E-Mail über alle Familien eindeutig; SaaS ohne Benutzernamenfeld. `alembic check` auf der Entwicklungsdatenbank sauber.
- 2026-11-17: Etappe 4.2 Verwaltungsbereich des Betreibers: Tabellen `operator`, `operator_session`, `signup_code`
  (Migration 0011). `cli create-operator` (interaktiv oder `KIDCTL_OPERATOR_USER`/`_PASSWORD`; `--reset` setzt Passwort und
  Authenticator neu und beendet Sitzungen). Anmeldung `/admin/login` mit Passwort + TOTP, eigener Cookie `kidctl_admin`
  (Pfad `/admin`, SameSite=Strict, 12 h), eigene Fehlversuchszähler; `ADMIN_ALLOW_NETS` (CIDR-Liste) – außerhalb 404.
  Übersicht `/admin`: je Familie Anzahl Eltern/Kinder/Geräte, Agent-Versionen, letzte Geräte- und Eltern-Aktivität,
  Geräte offline (> 48 h), Agent zu alt, Ruhemodus – nur unverschlüsselte Spalten im Plattformkontext. Einladungscodes
  `/admin/codes`: 12 Zeichen (XXXX-XXXX-XXXX), nur Hash gespeichert, einmalig, 30 Tage, mit Notiz; eingelöste Codes
  bleiben sichtbar (mit Familie), offene löschbar; `operators.find_code` für die Registrierung (4.3). Aufbewahrung:
  abgelaufene Betreiber-Sitzungen 30 Tage (`KIDCTL_KEEP_ADMIN_SESSIONS_DAYS`). Doku `docs/server(.de).md`, `.env.example`.
  Getestet (pytest, 6 neue Tests): Zugang nur mit Betreiber-Sitzung (Eltern-Sitzung gilt nicht und umgekehrt), Abmelden,
  Fehlversuchssperre, Übersicht ohne Namen/Inhalte, Codes (einmalig, tolerant bei Eingabe, eingelöste nicht löschbar,
  CSRF), IP-Beschränkung, CLI anlegen/zurücksetzen. Im Browser auf der Entwicklungsdatenbank angesehen (Login, Familien,
  Codes); `alembic check` sauber.
- 2026-11-17: Etappe 4.3 Registrierung: `REGISTRATION=open|invite|closed` (SaaS Standard `invite`, Einzelbetrieb immer
  `closed`, ungültiger Wert bricht den Start ab). `/signup`: E-Mail (+ Einladungscode) → Bestätigungslink per Mail (24 h,
  mit `SECRET_KEY` signiert, trägt E-Mail/Code/Ablauf – bis zur Bestätigung wird nichts gespeichert); immer dieselbe
  Antwort, bei vergebener Adresse keine Mail; ohne SMTP 503. `/signup/<link>`: Anzeigename, Passwort, Authenticator auf
  einer Seite (Code wird vor dem Anlegen geprüft) → Familie (eigener Schlüssel) + Inhaber-Account mit bestätigter E-Mail
  in einer Transaktion, Code atomar eingelöst (mit Familie), danach angemeldet auf der Übersicht („Kind anlegen“).
  Begrenzung je IP (Anfragen und falsche Codes, Zähler `signup`); Link auf der Anmeldeseite nur bei offener Registrierung.
  Getestet (pytest, 5 neue Tests): offener Ablauf bis zur eigenen Familie, Link danach unbrauchbar, vergebene Adresse
  ohne Mail; Einladungscode Pflicht, eingelöst mit Familie, zweiter Link mit demselben Code abgewiesen; Signatur/Ablauf;
  Einzelbetrieb 404; ohne SMTP 503; Fehlversuchssperre. Seiten im Browser im SaaS-Modus angesehen.
- 2026-11-17: Etappe 4.4 Support-Freigabe: Tabellen `support_grant`, `support_access`, `support_session` (Migration 0012).
  Inhaber gibt unter „Einstellungen → Support-Zugriff“ (nur SaaS) für 1/24/72 h frei, jedes Elternteil kann beenden.
  Betreiber öffnet in `/admin` bei freigegebenen Familien „Ansehen“ → Cookie `kidctl_support` (Pfad `/`, SameSite=Strict);
  `require_session` setzt dann den Familienkontext ohne Eltern-Sitzung. Gültig nur, solange Ansicht (max. 2 h),
  Freigabe und Betreiber-Sitzung gelten (Prüfung bei jedem Aufruf; Widerruf/Abmelden beendet sofort). Nur lesend: alle
  Aktionen laufen über `csrf_protect` und werden abgelehnt, `/profile` gesperrt, Formulare ausgegraut, Banner mit Familie,
  Betreiber und Ablauf, Abmelden/Profil im Kopf ersetzt durch „Support-Ansicht beenden“. Jeder Seitenaufruf (außer
  Chat-Aktualisierung) wird mit Betreiber und Pfad protokolliert, für alle Eltern sichtbar; Aufbewahrung 2 Jahre.
  Getestet (pytest, 5 neue Tests): Freigabe → Ansicht zeigt Kinder, Senden/Ändern/Profil 403, Protokoll; Widerruf durch
  anderes Elternteil beendet offene Ansicht; nur Inhaber gibt frei, nur 1/24/72 h; fremde Familie ohne Freigabe 403;
  abgelaufene Freigabe; Betreiber-Abmeldung beendet Ansicht; Einzelbetrieb ohne Bereich. Im Browser im SaaS-Modus
  angesehen (Banner, Einstellungen), Widerruf beendet die Ansicht auf dem laufenden Server.
- 2026-11-17: Etappe 4.5 Export/Import, Backup-Trennung, Familie löschen: `app/family_export.py` – ZIP mit
  `manifest.json` (`kidctl-family-export`, Version 1, Zeilen je Tabelle) und je Familientabelle einer JSON-Datei,
  entschlüsselt; ohne Eltern-Accounts, Einladungen, Kopplungscodes, Support-Daten. Import nur in eine leere Familie,
  neue IDs mit umgeschriebenen Verweisen, Verweise auf Eltern entfallen, Geräte kommen getrennt an (neu koppeln);
  ältere Versionen lesbar, neuere abgelehnt, Fehler → nichts übernommen. Einstellungen „Daten der Familie“: Export und
  Import nur Inhaber mit Passwort; im SaaS „Familie löschen“ (Passwort + „LÖSCHEN“): erst Ruhemodus an verbundene
  Agenten, dann `tenant.delete_family`. Im SaaS kein Datenbank-Backup für Eltern (Bereich ausgeblendet, Routen 404),
  Stand im Verwaltungsbereich ohne Download. Restlücke „offline Agent nach Löschen/Entfernen“ in `docs/limitations`.
  Doku `docs/backup(.de).md` (Umzug Self-Hosting ↔ gehostet), README und Website-Funktionsliste.
  Getestet (pytest, 6 neue Tests): Export → Import in andere Familie (Kinder, Regeln, Gerät getrennt, Gerätebenutzer,
  Nutzung, Chat entschlüsselt, Quelle unverändert), falsches Passwort, nicht leere Familie, kaputte/zu neue/beschädigte
  Dateien ohne Teilimport, nur Inhaber; SaaS ohne Backup-Bereich; Löschen mit Bestätigung, vorher Ruhemodus-Push,
  andere Familie bleibt; Einzelbetrieb ohne Löschen. Zusätzlich Export der Entwicklungsdatenbank (11 Tabellen,
  311 Zeilen) in eine Wegwerf-Familie importiert: alle Zeilenzahlen gleich, danach gelöscht. Agent-Tests (274) grün,
  `hugo --minify` fehlerfrei.
- 2026-11-17: Etappe 5 Abo-Vorbereitung (nur SaaS): `family.plan` (free | standard), `billing_status` (trial | active |
  canceled | past_due), `trial_ends_at`, `paid_until`, neutrale Felder `billing_provider`/`billing_customer`,
  `billing_notified`; `signup_code.plan` (Migration 0013 – bestehende Familien „Kostenlos“/aktiv). `app/billing.py`:
  Ablauf → 14 Tage Schonfrist (Mail an Inhaber + Push 14/7/1 Tage vorher, Banner) → Ruhemodus (`paused_at`, Policy ohne
  Zuordnungen an alle aktiven Geräte; WebUI nur Einstellungen, Profil, Abmelden, Push) → nach 90 Tagen automatische
  Löschung (Vorwarnung 30/7/1 Tage). Stündliche Prüfung im Hintergrund, sofort nach Änderung; einmal pausiert bleibt
  pausiert, bis wieder bezahlt ist. Registrierung: Testphase (`KIDCTL_TRIAL_DAYS`, 30) oder „Kostenlos“ per Code.
  Kindergrenze je Tarif vorbereitet (vorerst keine). Verwaltungsbereich: Abo-Spalte, Familienseite `/admin/families/<id>`
  zum Setzen von Tarif/Status/Daten/Anbieter/Kundennummer (wirkt sofort), Codes mit Option „Kostenlos“.
  Webhook `POST /api/billing/webhook` (Bearer `BILLING_WEBHOOK_SECRET`, sonst 404). Einstellungen: Karte „Abo“.
  SQLite-Übernahme verträgt Spalten, die alte Datenbanken nicht haben.
  Getestet (pytest, 8 neue Tests): Bestand kostenlos, Einzelbetrieb ohne Fristen; Testphase → Hinweise nur einmal je
  Stufe → Banner → Ruhemodus (Umleitung, Einstellungen mit Export, Policy-Push); Betreiber verlängert → Ruhemodus endet
  sofort mit Push; Löschung mit drei Vorwarnungen; Registrierung mit Testphase bzw. Code „Kostenlos“; Webhook
  (aus ohne Geheimnis, 401, wieder aktiv, überfällig mit Laufzeit, ungültig, unbekannt); Kindergrenze; Abo-Karte.
  Im Browser im SaaS-Modus: abgelaufene Testphase mit Banner und Abo-Karte, Übersicht und Familienseite; die
  Hintergrundprüfung beim Start hat den ersten Hinweis vermerkt. Server 205, Agent 274 Tests grün.
- 2026-11-17: Familienname und Rechnungsangaben: `crypto.PlatformSealed` (AES-GCM, Schlüssel per HKDF „kidctl platform
  data“ aus dem Hauptschlüssel, Präfix `p1:`), `family.name` darauf umgestellt (Migration 0014 verschlüsselt bestehende
  Namen), neue Tabelle `billing_address` (je Familie eine, CASCADE). Einstellungen: Karte „Familie“ (Name, nur Inhaber
  ändert), in der Abo-Karte Formular „Rechnungsangaben“ (nur SaaS, nur Inhaber, Pflichtfelder, E-Mail geprüft);
  `billing.address_needed` → Banner „Bitte hinterlegt eure Rechnungsangaben“. Verwaltungsbereich: Name in Übersicht und
  Familienseite (änderbar), Rechnungsangaben lesend, Markierung „Rechnungsangaben fehlen“. Nicht im Familien-Export.
  Getestet (pytest, 6 neue Tests): Name verschlüsselt gespeichert, im Verwaltungsbereich lesbar und änderbar, nur
  Inhaber; Rechnungsangaben: kein Hinweis in früher Testphase, Banner bei aktivem Abo, Pflichtfelder/E-Mail, gespeichert
  verschlüsselt, Banner weg, Anzeige beim Betreiber; Markierung in der Übersicht; Einzelbetrieb 404; Löschen der Familie
  entfernt sie. Migration 0014 auf der Entwicklungsdatenbank mit Klartext-Namen hin und zurück geprüft. Im Browser im
  SaaS-Modus angesehen. Server 211 Tests grün.
- 2026-11-18: Anforderungen Verwaltungsbereich 2 per /grill-me abgestimmt (Betreiber-Rollen, Verwaltungsprotokoll,
  Übersicht mit Suche/Sortierung/Kacheln, Detailansicht mit Bearbeiten pro Karte, Tickets, manuelle Rechnungen mit
  Archiv). Noch keine Umsetzung.
- 2026-11-18: Verwaltungsbereich 2, Etappe 1 (Rollen, Verwaltungsprotokoll): `operator.role` (admin | support, Bestand →
  admin), `email`, `ticket_mails`; Tabelle `admin_log` (Akteur, Familie ohne Fremdschlüssel, Objekt, Feld, alt/neu mit
  Plattform-Schlüssel; Migration 0015). Rollenprüfung als Abhängigkeit (`operators.require_admin`/`admin_csrf`):
  Einladungscodes, Familienname und Abo ändern nur Admin; Support sieht Familien, Protokoll, Profil, Support-Ansicht.
  `app/audit.py` (record/changes); protokolliert: Familienname, Abo-Felder (Betreiber und Webhook), Ruhemodus an/aus
  und automatische Löschung (System), Löschung durch den Inhaber, Codes angelegt/gelöscht, Rollenwechsel per CLI.
  `/admin/log` (Filter Familie/Akteur/Art, 50 je Seite, Parameter in der URL), `/admin/profile` (E-Mail, Ticket-Mails),
  CLI `create-operator --role --email`, `set-operator-role`. Aufbewahrung 2 Jahre, Rechnungs-Einträge ausgenommen.
  Getestet (pytest, 7 neue Tests): jede Admin-Route mit beiden Rollen (10 Routen), Bearbeiten/Codes-Reiter nur für
  Admin; Protokolleinträge für Name, Abo, Code, Webhook („Webhook“) und Ruhemodus („System“); Filter; Familie ohne
  Zugriff; Aufbewahrung behält Rechnungs-Einträge; CLI-Rollen; Profil. Seiten im Browser (Desktop, Handy) angesehen.
  Server 218 Tests grün.
- 2026-11-18: Verwaltungsbereich 2, Etappe 2 (Übersicht): Kacheln Familien · Kinder · Geräte · in Testphase · Testphase
  endet ≤ 7 Tage · Ruhemodus · offene Tickets (bis Etappe 4: 0) · letztes Backup (grün/rot), Klick filtert. Suche
  (ID bzw. `#ID`, Teilwort im entschlüsselten Namen, ohne Groß/klein), Filter-Chips Status/Tarif/Probleme (offline,
  veraltet, Rechnungsangaben, Ticket, Support-Freigabe), Tabelle ID · Familie · Seit · Kinder / Geräte · Agent (älteste
  Version, ⚠) · Letzte Aktivität (relativ, Datum im `title`) · Abo · Zustand; jede Spalte sortierbar (Klick dreht um),
  50 je Seite, Parameter `q`/`status`/`plan`/`issue`/`sort`/`page` in der URL, ohne JavaScript; mobil als Karten.
  „Ansehen“ (Support-Ansicht) jetzt auf der Familienseite. Abgeleitete Schlüssel werden zwischengespeichert; veraltete
  Agenten gegen die einmal geladenen Releases geprüft (vorher Dateisystem-Suche je Gerät).
  Getestet (pytest, 13 neue Tests): Kacheln, Suche (Teilwort, Groß/klein, #ID, ID, kein Treffer), Filter und
  Kombinationen, Chip-Links behalten übrige Filter, jede Sortierung auf- und absteigend gegen die Referenz, älteste
  Version und jüngste Aktivität, Blättern behält Parameter und begrenzt die Seite, relative Zeit mit/ohne Zeitzone.
  Lastmessung (`tests/perf_admin_overview.py`, 5.000 Familien, 20.000 Geräte, 10.000 Kinder): Übersicht ~155 ms je
  Aufruf (vorher ~430 ms), Suche/Filter/Sortierung/Seite 50 gleich schnell. Im Browser Desktop und Handy angesehen.
  Server 231 Tests grün.
- 2026-11-18: Verwaltungsbereich 2, Etappe 3 (Detailansicht): Karten-Raster (mobil einspaltig) Kopf · Abo ·
  Rechnungsangaben (lesend) · Nutzung (Eltern/Kinder/Geräte, je Gerät Nummer, Plattform, Agent, zuletzt gesehen – ohne
  Namen) · Support (Freigabe, „Ansehen“, letzte Freigabe, Zugriffsprotokoll; Tickets ab Etappe 4) · Rechnungen
  (Platzhalter) · Verlauf (letzte 15 Protokolleinträge, Link zu `/admin/log?family=`). Name und Abo zeigen Werte und
  „Bearbeiten“; `?edit=name|abo` macht die Karte zum Formular mit Speichern/Abbrechen (eigenes Formular, CSRF), nach dem
  Speichern zurück zur Karte. Support sieht keinen Bearbeiten-Knopf, `?edit` wird für ihn ignoriert. Die Detailansicht
  lädt nur noch die Metadaten der einen Familie (`operators.families(only=…)`).
  Getestet (pytest, 4 neue Tests): ohne `?edit` keine Eingabefelder, mit `?edit=name|abo` genau die Felder, ungültige
  Karte ignoriert; Speichern je Karte mit Rücksprung, Abo-Änderung beendet den Ruhemodus sofort, Policy-Push, Einträge
  im Verlauf (Betreiber und System), Fehler lässt das Formular offen; Support ohne Bearbeiten (auch per URL/POST);
  Support-Karte mit Freigabe, „Ansehen“ und Zugriffsprotokoll. Rollen-Test angepasst. Im Browser Desktop und Handy
  angesehen. Server 235 Tests grün.
- 2026-11-18: Verwaltungsbereich 2, Etappe 4 (Tickets, nur SaaS): Tabellen `ticket`, `ticket_message` (Familien-
  bezogen, CASCADE; Betreff, Nachrichten, Autor, Metadaten mit dem Plattform-Schlüssel; Migration 0016), `app/tickets.py`.
  Eltern: Menüpunkt „Support“ (auch im Ruhemodus) mit Liste, Anlegen (Betreff, Kategorie Technik/Abo & Rechnung/
  Sonstiges, Nachricht, Hinweis zur Lesbarkeit und Support-Freigabe), Verlauf ohne interne Notizen, Antworten,
  Schließen. Beim Anlegen angehängt: Agent-Versionen, Plattformen, Tarif/Status/Ruhemodus, Sprache. Status: neu →
  wartend/in Bearbeitung (Support) → in Bearbeitung (Familie), Wiedereröffnen nur bis 14 Tage nach Schließen,
  automatisch geschlossen nach 14 Tagen „wartend“ (stündlicher Job, Hinweis an die Familie). Verwaltung: Reiter
  „Tickets“ (beide Rollen) mit Filter Status (Standard offen)/Kategorie/Familie/Betreff, Sortierung, 50 je Seite;
  Ticketseite mit Metadaten, Verlauf inkl. interner Notizen (farbig), Antwort mit „intern“ und Status danach,
  Statusauswahl, „zuletzt bearbeitet von“; Tickets in der Support-Karte der Familie, Kachel „offene Tickets“.
  Benachrichtigungen nur Hinweis + Link: neu/Antwort der Familie → Betreiber mit Ticket-Mails und
  `SUPPORT_NOTIFY_EMAIL`; Antwort des Supports/automatisches Schließen → alle Eltern per Mail und Push; interne Notizen
  nichts. Statuswechsel im Verwaltungsprotokoll (Betreiber, „Familie“, „System“). Aufbewahrung 12 Monate nach Schließen
  (`TICKET_RETENTION_DAYS`), mit der Familie gelöscht, nicht im Export. README ergänzt.
  Getestet (pytest, 7 neue Tests): Menüpunkt nur im SaaS; Lebenszyklus mit allen Statusübergängen inkl.
  Wiedereröffnen innerhalb/außerhalb 14 Tage, Support-Rolle antwortet, interne Notiz für Eltern unsichtbar und ohne
  Benachrichtigung, Empfänger und Inhaltsfreiheit der Mails, Verschlüsselung in der Datenbank, Protokoll;
  automatisches Schließen; Isolation zwischen Familien (Ansehen, Antworten, Schließen per ID → 404); Erreichbarkeit im
  Ruhemodus; Liste mit Filtern/Sortierung, Kachel, Support-Karte; Aufbewahrung und Löschen mit der Familie.
  Im Browser angesehen (Eltern-Verlauf, Ticketseite mit interner Notiz). Server 242 Tests grün.
- 2026-11-18: Verwaltungsbereich 2, Etappe 5 (Rechnungen, nur Admin): Tabellen `invoice_settings`, `price_item`,
  `invoice_counter`, `invoice`, `invoice_line` (Migration 0017); `app/invoices.py`, PDF mit ReportLab 4.4.10 (neu in
  `requirements.txt`). Beträge in Cent; Position = Menge × Einzelpreis; Regelbesteuerung: USt einmal auf die
  Nettosumme, kaufmännisch gerundet (halbe Cent vom Nullpunkt weg). Rechnungs-Einstellungen (Absender, Steuernummer,
  Kontakt, Bank, Fußzeile) und Preisliste in der Datenbank, protokolliert; `INVOICE_TAX_MODE`, `INVOICE_NUMBER_FORMAT`,
  `INVOICE_DUE_DAYS` aus `.env` (ungültig → Start bricht ab). Entwurf aus der Preisliste (Leistung ab Tag nach
  „bezahlt bis“), Positionen frei (negativ = Rabatt) → Ausstellen: Nummer aus dem Jahreszähler unter Zeilensperre,
  Datum, Fälligkeit, Kopie von Rechnungsadresse und Absender, Beträge und Steuermodus eingefroren, PDF (Plattform-
  Schlüssel), Mail mit PDF-Anhang und Zahlungsinformationen (Verwendungszweck = Nummer); „Erneut senden“. Bezahlt →
  Abo aktiv, „bezahlt bis“ = Ende des Leistungszeitraums (wenn später), Ruhemodus endet sofort. Storno-Rechnung
  (eigene Nummer, „Storno zu Rechnung … vom …“, negative Beträge), Original „storniert“; Storno einer bezahlten zeigt
  „Abo prüfen“, Abo unverändert. Unveränderlichkeit auch per Route (409). `/admin/invoices` (Filter Jahr/Status/
  Familie, Summen je Jahr, CSV-/ZIP-Export), Karte „Rechnungen“ in der Detailansicht, Eltern unter Einstellungen → Abo
  mit PDF (nur eigene Familie). Familie löschen: nur Entwürfe weg, ausgestellte bleiben („gelöschte Familie #…“);
  Löschung nach 8 Jahren ab Ende des Ausstellungsjahres samt Protokolleinträgen. `mail.send` kann Anhänge. Muster
  beider Steuermodi und Storno: `dev/sample_invoices.py` → `dev/musterrechnung-*.pdf`.
  Getestet (pytest, 21 neue Tests): Geldformat/-eingabe; beide Steuermodi und Rundung; Vorbelegung aus der Preisliste;
  Ausstellen friert ein, Nummern lückenlos, auch bei 6 gleichzeitigen Ausstellungen; Pflichtangaben; bezahlt ↔ Abo und
  Ruhemodus; Storno; Familie löschen → Rechnungen lesbar; Frist; Export; voller Ablauf über die Oberfläche inkl. Mail
  mit PDF; Unveränderlichkeit per manipulierter Anfrage; jede Rechnungs-Route mit beiden Rollen; fremde Familie ohne
  PDF-Zugriff. Migrationen 0015–0017 gegen eine Datenbank mit Stand 0014 und Bestandsdaten (Familie, Betreiber →
  Admin) hoch/runter/hoch geprüft. Muster-PDFs angesehen, Seiten im Browser (Desktop, Handy). Server 258, Agent 274
  Tests grün.
- 2026-11-18: Version 0.34.0 (Agent, Pakete): Sammelversion für SaaS-Etappe 4/5, Familienname, Rechnungsangaben und
  Verwaltungsbereich 2 – keine Änderung am Agenten selbst. News 0.34 (DE/EN). Getestet: Versionsabgleich
  PKGBUILD/Agent, `hugo --minify` fehlerfrei.
- 2026-11-18: Code-Scanning (CodeQL) ausgewertet, Sicherheitsfix 0.34.1 (Schwere niedrig, betroffen 0.26.0–0.34.0):
  Weiterleitungen nach dem Sprachumschalter konnten auf eine fremde Adresse zeigen. Neu `web.is_local_path`/
  `local_redirect`: nur lokale Pfade; alle Weiterleitungen in Eltern-, Betreiber- und Ticket-Routen laufen darüber.
  Test zum Bericht prüft die Domain per Regex statt Teilstring. Übrige Befunde bewertet und mit Begründung geschlossen:
  Weiterleitungen mit ganzzahligen IDs (kein Angriffsweg), CLI zeigt den TOTP-Schlüssel beim Einrichten im Terminal
  (gewollt), Fehlermeldungen der Passwortprüfung enthalten das Passwort nicht. News 0.34.1 (DE/EN) mit Verweis auf das
  Security Advisory. Getestet (pytest, 2 neue Tests): lokale und fremde Ziele; Sprachumschalter leitet fremde Ziele auf
  `/login`. Server 260 Tests grün, Versionsabgleich PKGBUILD/Agent.
- 2026-11-18: README-Banner im Design der Projektseite: `dev/banner.py` (Dry-Run, `--apply` schreibt) rendert mit
  Playwright das Theme-CSS, die Texte aus `website/i18n/` und WebUI-Screenshots nach
  `docs/img/<de|en>/banner-<light|dark>.webp`; README.md/README.de.md binden es per `<picture>` (hell/dunkel) mit
  Link auf die Projektseite ein. Getestet: alle vier Bilder gerendert und angesehen, Doku-Parität ok.
- 2026-11-18: Layoutfix Verwaltungsbereich: Karten-Raster (`.card-grid`) bekommt Abstand nach unten, damit es nicht
  an die folgende Karte stößt (Rechnungs- und Ticket-Detail); Formular „Als bezahlt markieren“ behält das
  Flex-Layout (`form.row.inline`), Button steht neben dem Datum; Rechnungssumme rechtsbündig (`p.right`).
  Getestet im Browser (Dev-Server, Markup von Rechnungs- und Ticket-Detail): Abstände und Ausrichtung korrekt.
- 2026-11-18: Verwaltungsbereich, Familienübersicht: Suche neu gestaltet – Suchfeld mit Lupe und „Suchen“, darunter
  aufklappbar **Erweitert** (ohne JavaScript, `<details>`) mit Auswahllisten für Status, Tarif, Auffälligkeit und neu
  „Ohne Aktivität seit“ (über 7/30/90 Tage, nie aktiv zählt mit) sowie „Registriert ab/bis“; aktive Filter als
  entfernbare Chips („Alle entfernen“), Zähler am „Erweitert“. Leere Formularfelder leitet der Server auf eine
  saubere Adresse um; ungültige Daten werden ignoriert. Getestet: pytest (2 neue Tests: Zeitraum-/Inaktivitätsfilter,
  ungültiges Datum, Umleitung, Chip-Links), Server 262 Tests grün; im Browser Desktop und mobil (zwei Spalten,
  Absenden über „Filter anwenden“ → `/admin?plan=free`).
- 2026-11-18: Rechnungs-Detail: Aktionsleiste (Zahlungsdatum, „Als bezahlt markieren“, „Stornieren“, „Erneut senden“)
  unten bündig (`.row.bottom`), damit alle Buttons auf einer Linie stehen. Getestet im Browser: Unterkanten von
  Datumsfeld und allen Buttons identisch.
- 2026-11-18: Verwaltungsbereich, eigenes Profil (`/admin/profile`, beide Rollen): Karten Konto (Benutzername, Rolle,
  E-Mail, Ticket-Mails), Passwort ändern (aktuelles Passwort, Fehlversuche zählen wie bei der Betreiber-Anmeldung),
  neuer Authenticator (QR-Code + Schlüssel, Bestätigung mit Passwort und Code; vorbereiteter Schlüssel per HMAC
  signiert, 15 Minuten gültig, alter gilt bis zur Bestätigung) und Sitzungen (Anzahl, „Andere Sitzungen abmelden“).
  Passwort-/Authenticator-Wechsel beenden alle anderen Sitzungen, schreiben ins Verwaltungsprotokoll (Objekt
  „Betreiber-Zugang“, ohne Werte) und melden sich per Mail; E-Mail-Änderungen werden protokolliert. Benutzername und
  Rolle bleiben Kommandozeile. Getestet: pytest (9 neue Tests, je Rolle admin/support: E-Mail, Passwort inkl. falsches
  Passwort/zu kurz/Wiederholung, Authenticator inkl. falscher Code, gefälschtes Token, Anmeldung mit neuem Code,
  Sitzungen abmelden, CSRF, ohne Anmeldung), Server 271 Tests grün; im Browser Profilseite angesehen.
- 2026-11-18: Anforderungen abgestimmt (noch nicht umgesetzt): **Whitelabel** – eigener Name, Logo und Favicon,
  eingestellt im Verwaltungsbereich (Logo-Upload, DB, protokolliert; Ausnahme von „Konfiguration nur über `.env`“),
  wirkt auf Eltern-WebUI, PWA, Anmeldung/Registrierung und alle Mails; Untertitel immer „powered by KidCTL“;
  Kinder-Clients bleiben KidCTL. **Newsletter** – Bereich im Verwaltungsbereich zum Versand an alle Familien;
  Opt-in je Familie (Standard aus, Schalter in den Einstellungen und bei der Registrierung, Abmeldelink in jeder Mail);
  Betreff und Text je Newsletter Deutsch und optional Englisch, jedes Elternteil bekommt seine Sprache.
- 2026-11-18: **Whitelabel** umgesetzt: Verwaltungsbereich „Erscheinungsbild“ (nur Admin, Tabelle `branding`,
  Migration 0018) mit Name, Logo (PNG, auf ≤ 512×128 verkleinert, Option „nur Logo“) und App-Icon (quadratisch
  512×512, daraus 192/180/32 px); Upload per Pillow geprüft (PNG/JPEG/WebP/GIF/ICO, ≤ 2 MB, ≤ 25 MP, kein SVG) und
  neu als PNG gespeichert; jede Änderung im Verwaltungsprotokoll (Objekt „Erscheinungsbild“, Bilder nur „neu“/„entfernt“).
  Wirkung: Kopfzeile und Anmeldeseiten (Logo + Name + „powered by KidCTL“), Seitentitel, Abo-Hinweise, PWA-Manifest
  und Icons (`/brand/icon-<n>.png`, `/brand/logo.png`, ohne eigenes Icon das KidCTL-Icon), Push-Titel/-Texte, alle
  Mails zentral in `mail.send` (Name statt „KidCTL“, Fußzeile „<Name> · powered by KidCTL“), Nutzungsbericht-Mail,
  Authenticator-Name (`TOTP_ISSUER` jetzt optional, Standard: Name). Kinder-Clients, Installer-Hinweise und
  Export-Texte bleiben KidCTL. Branding 30 s zwischengespeichert (mehrere Worker). Getestet: pytest (6 neue Tests:
  Standard, Name/Logo/Icon inkl. Skalierung und Protokoll, Ablehnung von Nicht-Bildern, Support-Rolle 403, Entfernen,
  Mail-Fußzeile über echten `mail.send` mit Fake-SMTP, Authenticator-Name), Server 277 Tests grün; Migration check
  und down/up; im Browser Admin-Seite, Eltern-Oberfläche desktop und mobil mit Testlogo.
- 2026-11-18: Fix Eltern-WebUI/PWA (SaaS): Die untere Navigationsleiste hatte fest 5 Spalten, der sechste Eintrag
  „Support“ rutschte halb abgeschnitten in eine zweite Zeile. Jetzt so viele Spalten wie Einträge, Icon-Fläche
  schrumpft mit; „Einstellungen“ heißt in der unteren Leiste im SaaS kurz „Optionen“ (für Screenreader weiter
  „Einstellungen“, Seitenleiste ab 840 px unverändert). Self-Hosting (5 Einträge) unverändert. Getestet im Browser:
  375 px alle sechs Einträge vollständig in einer Zeile, 1100 px Seitenleiste mit „Einstellungen“.
- 2026-11-18: **Newsletter** umgesetzt (nur SaaS): Familie stimmt zu (Opt-in, `Family.newsletter_at`, Standard aus,
  Bestand ohne Zustimmung) per Häkchen bei der Registrierung oder Karte „Neuigkeiten per E-Mail“ in den
  Einstellungen (jedes Elternteil, auch im Ruhemodus). Verwaltungsbereich „Newsletter“ (nur Admin, Tabelle
  `newsletter`, Migration 0019): Entwürfe mit Betreff/Text Deutsch und optional Englisch (nur beides oder keins),
  „Speichern und Test an mich“ (beide Sprachen an die Profiladresse), „An N Familien senden“ (Statuswechsel atomar,
  nur einmal; Versand im Hintergrund an alle Eltern mit bestätigter Adresse, Sprache je Elternteil, ohne Englisch
  Deutsch; Anrede, Abmeldehinweis mit HMAC-signiertem Link, Header `List-Unsubscribe`/`List-Unsubscribe-Post`),
  danach unveränderlich im Archiv mit Zahl der Familien und Mails, Versand im Verwaltungsprotokoll. Abmeldeseite
  `/newsletter/unsubscribe/<Familie>/<Signatur>`: GET bestätigt nur (Mail-Scanner), POST bestellt ab (auch RFC 8058).
  `mail.send` kann zusätzliche Header. Getestet: pytest (6 neue Tests: Schalter in den Einstellungen inkl. CSRF,
  Registrierung mit/ohne Häkchen, Abmeldelink inkl. falscher Signatur und GET ohne Wirkung, Testversand DE+EN und
  Versand nur an zustimmende Familie mit Abmeldelink/Headern, Doppelversand/Ändern/Löschen danach 409, Sprachwahl
  mit Rückfall, Pflichtfelder und Support-Rolle 403), Server 283 Tests grün; Migration check und down/up; im Browser
  Entwurf, Versand-Karte und Einstellungskarte.
- 2026-11-18: Version 0.35.0 (Agent, Pakete): Sammelversion für Familiensuche, Admin-Profil, Whitelabel, Newsletter
  und den Fix der PWA-Leiste – keine Änderung am Agenten selbst. News 0.35 (DE/EN), Roadmap mit Version. Getestet:
  Versionsabgleich PKGBUILD/Agent, `hugo --minify` fehlerfrei.
- 2026-11-19: Anforderungen per /grill-me abgestimmt: Spielerkennung Windows (Steam-Fehler, Manifeste für EA,
  Ubisoft, Battle.net, Wargaming, GOG, Prozess-Abstammung als Rückfall), einheitliche HTML-Mails mit Logo und
  Footer (Anbieter-Angaben, Newsletter in Markdown, Mail-Vorschau), Windows-Oberfläche im Fluent-Stil. Noch keine
  Umsetzung.
- 2026-11-19: Version 0.36.0 (Agent): Spielerkennung unter Windows erweitert (siehe „Spielerkennung erweitert“):
  Steam-Fehler behoben (Index bei Änderung neu lesen, Steam-Pfad je Benutzer). Neu: GOG, EA app, Battle.net.
  Rückfall über die Prozess-Abstammung mit Hilfsprozess-Liste, Wargaming als Launcher. Restlücken für
  Ubisoft/Wargaming und Lutris/Bottles, Roadmap. Getestet:
  - Agent-Tests 285 grün, davon neu: echte Launcher-Daten, robuster `product.db`-Leser, Registry-Signatur,
    Launcher-Ordner, Abstammung, 8.3-Pfade.
  - Windows-11-VM, Konto des Kindes:
    - Bridge Constructor Portal (Steam), SimCity 2000 (EA), Absolute Drift (Epic) und Bio Menace (GOG) werden mit
      Namen als Spiel gezählt und im Dashboard als „spielt gerade“ angezeigt.
    - Ein entferntes und wieder zurückgelegtes Steam-Manifest wird binnen 20 s neu gelesen.
    - Spiele-Limit beendet SimCity.
    - Launcher und `EALocalHostSvc.exe` zählen nicht.
  - Nicht live getestet: Battle.net (Hearthstone-Download zu groß), Vorwarnung vor dem Spiele-Ende (Limit war sofort
    erreicht; Unit-Tests decken sie ab).
- 2026-11-19: Version 0.37.0 (Server): einheitliche HTML-Mails (siehe „E-Mails“).
  - Alle 13 Mailtypen erben von `mail/base.html`: Logo per CID, hell/dunkel, Button auch für Outlook, Textfassung,
    Fußzeile mit Grund, Links und Anbieter-Angaben.
  - Block „Anbieter“ auf der Seite „Erscheinungsbild“.
  - Newsletter in Markdown mit Vorschau.
  - Mail-Vorschau `/admin/mail-preview` mit „Testmail an mich“.

  Getestet:
  - Server-Tests 315 grün, neu `test_mails.py`: jeder Mailtyp DE/EN, Whitelabel- und Anbieter-Fußzeile,
    Einzelbetrieb, CID-Aufbau, HTML < 100 KB, keine externen Ressourcen, Rollen der Vorschau, Markdown ohne HTML,
    Bilder und `javascript:`.
  - Migration 0020 hin und zurück, `alembic check`.
  - Im Browser:
    - Mail-Vorschau inkl. Logo.
    - Passwort-Mail hell, Rechnung und Newsletter dunkel.
  - MIME-Aufbau der `.eml`-Fassungen: Text, HTML mit Logo als `multipart/related`, PDF-Anhang.
- 2026-11-19: Version 0.38.0 (Tray unter Windows): Oberfläche im Stil von Windows 11 (siehe „Oberfläche im
  Windows-11-Stil“). Umgestellt: alle Fenster, Tray-Menü, monochromes Tray-Symbol mit Statuspunkt, Toast mit App-Logo
  und Installer. Linux unverändert. Dazu Doku zum Kinderrechner (Windows) inkl. der in 0.36 erweiterten Spieleliste.
  Getestet:
  - Tray-Tests 26 grün, neu: Linux-Layout unverändert, Windows-Layout/Symbol/Toast-Logo erzwungen, Show-Hook mit
    `QWindow`.
  - `dev/winshots.py` in der Windows-11-VM (Qt 6.12): alle Fenster hell/dunkel × DE/EN.
  - Echter Toast „Nachricht von mama“ per QMP-Abzug.
  - Installer-Vorschau mit Inno Setup 6.7.4 kompiliert.

  Dabei gefunden und behoben:
  - Der Show-Hook stürzte bei `QWindow`-Ereignissen ab.
  - Das Stylesheet schaltete die native Zeichnung von Radio-Knöpfen und Zahlenfeldern ab.
- 2026-11-20: Anforderungen per /brainstorming abgestimmt: Login in zwei Schritten, Passkeys als vollständiger Login,
  Homepage-Link auf der Loginseite, Einladungsmail, Verwaltungsnavigation wie Elternoberfläche, Tray-Symbol Linux wie
  Windows, Screenshot Tray, Design-Durchsicht, automatische Sicherheitsprüfungen mit Badges (OpenSSF Scorecard, Best
  Practices, ZAP-Baseline/Observatory), anschließend Sicherheits- und Performance-Audit der Codebasis; Recherche
  Google-Login (Bau später). Noch keine Umsetzung.
- 2026-11-20: Version 0.39.0 (Server): Login in zwei Schritten für Eltern und Betreiber. Erst Passwort, dann
  Authenticator-Code; dazwischen ein Login-Ticket (Tabelle `login_ticket`, Migration 0021, nur der Hash; Cookie
  `HttpOnly`, `SameSite=Strict`; 5 min, einmalig, max. 5 Codes, an Konto und Art gebunden; abgelaufene Tickets
  räumt das Anlegen neuer mit weg). Abgelaufen bzw. ungültig führt zurück zu Schritt 1 mit neutralem Hinweis. Die
  Folge-Flows (Passwort vergessen, Authenticator-Link) bleiben unverändert und führen zum neuen Login. Anmeldeknöpfe
  bekommen die Klasse `btn-block` statt Inline-Styles. Getestet:
  - Server-Tests 324 grün, neu `test_login_steps.py`:
    - Reihenfolge; ohne Code nicht angemeldet.
    - Unbekannte Adresse und falsches Passwort: gleiche Antwort, je genau eine Hash-Prüfung.
    - Ticket einmalig, abgelaufen und an die Art gebunden; nur der Hash wird gespeichert.
    - 5 falsche Codes beenden das Ticket und sperren den Authenticator.
    - Betreiber-Login identisch.
    - Formulare ohne JavaScript.
  - Migration 0021 hin und zurück, `alembic check`.
  - Im Browser: Passwort → Code → Übersicht.
- 2026-11-20: Version 0.40.0 (Server): Homepage-Link und Einladungsmail.
  - Homepage-Link: Felder unter *Erscheinungsbild* (`homepage_url`, Linktext DE/EN; nur https, protokolliert). Der
    Link steht unter Login, Code-Schritt, Registrierung, Passwort vergessen/neu, Authenticator-Einrichtung,
    Einladung und Betreiber-Login; leer zeigt er auf die Projektseite.
  - Einladungscodes optional mit E-Mail-Adresse und Sprache: Mail `signup_invite` mit Code, Link
    `/signup?code=…`, Ablauf und Tarif (Vorschau und Tests DE/EN). „Erneut senden“ erzeugt einen neuen Code und
    löscht den alten.
  - Die Adresse liegt mit dem Plattform-Schlüssel in `signup_code.email` (Migration 0022). Sie wird beim Einlösen
    gelöscht, nach Ablauf im Aufräumjob. Bei der Registrierung per Link ist die Adresse vorbelegt und änderbar; die
    Bestätigung per Mail bleibt.

  Getestet:
  - Server-Tests 329 grün, neu `test_invite_mail.py`:
    - Link nur mit https, protokolliert, DE/EN, leer gilt der Standard.
    - Mail EN mit Code und Link; Liste maskiert, Adresse verschlüsselt gespeichert.
    - Erneut senden: alter Code ungültig, neuer mit gleichem Tarif und gleicher Notiz.
    - Registrierungslink belegt Code und Adresse vor, die Adresse ist änderbar; nach dem Einlösen bzw. Ablauf ist sie
      gelöscht.
  - Migration 0022 hin und zurück.
- 2026-11-20: Version 0.41.0 (Server): Verwaltungsbereich mit der Navigation der Elternoberfläche.
  - `base.html` zeigt für Betreiber die `.navbar` mit den Bereichen je Rolle (`web.admin_nav`) und neuen Icons
    (Familien, Rechnung, Schlüssel, Newsletter, Mail, Palette, Protokoll, Mehr).
  - Desktop: alle Bereiche in der Leiste links. Lange Wörter bekommen weiche Trennstriche, da Browser Deutsch nicht
    zuverlässig trennen.
  - Handy: Familien, Tickets, Rechnungen (Support: Protokoll) und „Mehr“ (`/admin/more` mit den übrigen Bereichen,
    Profil und Abmelden).
  - Profil und Abmelden stehen oben rechts; der Seitenkopf zeigt nur den Titel, die Reiter entfallen.

  Getestet:
  - Server-Tests 333 grün, neu `test_admin_nav.py`: je Rolle alle Bereiche erreichbar, genau ein aktiver Eintrag
    (auch auf der Familien-Detailseite), Handy-Leiste mit 3 + „Mehr“, keine Reiter, Rechte weiter serverseitig.
  - Im Browser bei 1280 und 375 px.
- 2026-11-20: Version 0.42.0 (Tray Linux): Tray-Symbol unter Linux wie unter Windows.
  - Monochromes KidCTL-Symbol mit Statuspunkt (`winstyle.tray_icon`), passend zur Leiste: Plasma nach Farbschema;
    GNOME ab 51 ebenso (obere Leiste hell oder dunkel), ältere GNOME immer dunkel. In der VM festgestellt, dass
    GNOME 51 eine helle Leiste hat.
  - Neu gezeichnet beim Wechsel des Farbschemas. Die Restzeit steht in Tooltip und Menü, `icon_text` entfällt.
  - Restlücke: GNOME (AppIndicator) zeigt keinen Tooltip, die Restzeit steht im Menü (`docs/limitations*.md`).
  - `dev/vm/vmctl.py` kann die Maus bewegen (Tooltip) und rechtsklicken (Menü).

  Getestet:
  - Tray-Tests 27 grün (Leistenhelligkeit GNOME 46/51 und Plasma, Symbol unter Linux).
  - Arch-VM, Plasma (helle Leiste): Symbol, grüner Punkt, Tooltip und Menü mit Restzeit.
  - Arch-VM, GNOME 51 mit AppIndicator: Symbol dunkel auf heller Leiste, Menü mit Restzeit, kein Tooltip.
  - Windows-11-VM: Tray-Menü und Tooltip hell/dunkel (DE) als Desktop-Abzug in voller Auflösung. EN fehlt, die VM
    hat nur Deutsch als Anzeigesprache. Einbindung in Startseite und Wiki erst nach Abnahme.
- 2026-11-20: Automatische Sicherheitsprüfungen (ohne neue Version, nur Workflows und Website).
  - `scorecard.yml`: OpenSSF Scorecard wöchentlich, bei Push und von Hand; `permissions: read-all`; Ergebnis
    veröffentlicht, SARIF in Code Scanning.
  - `websecurity.yml`: monatlich, passive ZAP-Baseline und MDN Observatory gegen `kids.nossco.de`. Bei Funden ab
    Medium bzw. einer Note unter A wird ein Issue nur mit Link auf den Lauf angelegt.
  - Alle Actions in allen Workflows per Commit-SHA gepinnt.
  - Startseite: Box „Automatisch geprüft“ (Abschnitt Vertrauen). Werte holt der Pages-Workflow
    (`data/checks.json`, wöchentlich aufgefrischt), als Text mit Link, keine externen Bilder; Best Practices
    erscheint erst mit `params.bestpractices_id`.
  - README: Scorecard-Badge mit Hinweis „Automatisch geprüft“ und Verweis auf `SECURITY.md`.
  - `dev/openssf-best-practices.md`: Antworten für alle Passing-Kriterien; offen sind `CONTRIBUTING.md`, ein
    Test-Workflow, ein Linter und der Branch-Schutz.

  Getestet:
  - `hugo --minify --panicOnWarning` mit und ohne `checks.json`.
  - YAML der Workflows geprüft, keine ungepinnten Actions mehr.
  - Läufe der Workflows erst nach dem Push auf `main` möglich.
- 2026-11-20: Screenshot „Tray-App mit Restzeit“ (Windows 11, DE und EN) auf der Startseite (Funktion „Was das
  Kind sieht“, `static/img/client/tray-<lang>.webp`) und im Wiki (`docs/img/<lang>/tray-windows.webp`,
  Kinderrechner Windows). Für die englischen Bilder wurde in der Test-VM das Sprachpaket en-US installiert; `kind`
  läuft danach wieder auf Deutsch. Desktop-Abzüge per QMP in voller Auflösung, Tooltip und Menü je hell/dunkel unter
  `build/winshots/desktop/` (nicht im Repo). Getestet: `hugo --minify --panicOnWarning`, Doku-Parität, Bild je
  Sprache auf Start- und Wiki-Seite.
- 2026-11-20: Version 0.43.0 (Server): Passkeys als vollständiger Login (Eltern und Betreiber).
  - Bibliothek `webauthn` 3.0.1, gepinnt mit Abhängigkeiten.
  - Neue Tabellen `passkey` (Plattform, Name mit Plattform-Schlüssel) und `webauthn_challenge` (nur der Hash des
    Tokens, einmalig, 5 Minuten), dazu `confirmed_at` an den Sitzungen (Migration 0023).
  - Loginseite: E-Mail-Feld mit `autocomplete="username webauthn"` (Conditional UI) und Knopf „Mit Passkey
    anmelden“ (`static/passkey.js`, ohne Framework, ohne JS versteckt).
  - Profil (Eltern und Betreiber): Liste, anlegen, umbenennen, löschen. Hinzufügen und Löschen nur nach frischer
    Bestätigung.
  - Gemeinsamer Mechanismus `passkeys.confirm` für Passwort und Authenticator ändern: Passwort + Code oder ein
    Passkey, 5 Minuten gültig. Ohne JS stehen die Felder direkt im jeweiligen Formular.
  - Neuer Mailtyp `passkey_notice` (hinzugefügt, entfernt, bei Wiederherstellung entfernt); bei Betreibern auch
    ein Eintrag im Verwaltungsprotokoll.
  - „Passwort vergessen“, Authenticator-Reset durch ein Mitelternteil und die CLI-Resets löschen alle Passkeys des
    Kontos, ebenso das Löschen eines Elternteils oder einer Familie.
  - Sign-Count-Rückgang bei nicht synchronisierten Schlüsseln wird abgelehnt und protokolliert.
  - `WEBAUTHN_RP_ID` (Standard: Host aus `BASE_URL`), Origin = `BASE_URL`.
  - Doku: `docs/webui*.md` (Anmelden und Passkeys, Domainbindung), `docs/server*.md`.

  Getestet:
  - Server-Tests 345 grün, neu `test_passkeys.py` mit Software-Authenticator (`tests/softauthn.py`):
    - Anlegen und Anmelden ohne Passwort und Code; ohne frische Bestätigung kein Hinzufügen, Löschen, Passwort- oder
      Authenticator-Wechsel.
    - Falscher Origin bzw. falsche RP-ID abgelehnt, ebenso eine wiederverwendete Challenge und ein Sign-Count-
      Rückgang; synchronisierte Passkeys mit Zähler 0 funktionieren.
    - „Passwort vergessen“ und Authenticator-Reset entfernen alle Passkeys.
    - Betreiber; Passkey der falschen Kontoart abgelehnt; klassischer Login unverändert.
  - Chromium (Playwright) mit virtuellem Authenticator gegen `localhost`: anlegen, Anmeldung per Conditional UI und
    per Knopf, Bestätigung per Passkey, löschen; ohne JavaScript kein Knopf.
  - Migration 0023 hin und zurück.
- 2026-11-20: Entschieden (geplant, noch nicht umgesetzt): Rechnungs-PDF bekommt eine Kopfzeile mit Logo und Name
  des Dienstes aus dem Erscheinungsbild; nur für neu ausgestellte Rechnungen, gespeicherte PDFs bleiben unverändert.
  Kein Roadmap-Eintrag (Abrechnungsdetail). Ohne Code, daher ohne Test.
- 2026-11-20: Transparenz zur Entwicklung mit KI (ohne neue Version): README-Abschnitt „Wie kidCTL entsteht“ (DE/EN)
  und Website-Seite „So entsteht kidCTL“ (`/how-kidctl-is-built/`, verlinkt in Fußzeile und Box „Automatisch
  geprüft“). Getestet: Doku-Parität, `hugo --minify --panicOnWarning`, Links in der erzeugten Seite.
- 2026-11-20: Beitragsregeln, Test-Workflow und Linter für das OpenSSF-Best-Practices-Badge (ohne neue Version).
  - `CONTRIBUTING.md`/`CONTRIBUTING.de.md` (inhaltsgleich, im Paritäts-Check und im README verlinkt): Issue zuerst,
    dann Pull Request; Testpflicht; Tests der drei Komponenten starten; `ruff check`; Doku-Parität;
    Sicherheitsmeldungen nur nach `SECURITY.md`; Abschnitt „KI-Unterstützung“ (gleiche Regeln, Kennzeichnung per
    `Co-Authored-By`).
  - `.github/workflows/tests.yml` bei Push auf `main` und Pull Requests, `permissions: contents: read`, Actions per
    SHA gepinnt: Linter und Doku-Parität; Server und Agent (inkl. E2E) gegen `postgres:17` als Service-Container,
    `pg_dump`/`pg_restore` der Backup-Tests per `docker exec` im Container; Tray mit PySide6 offscreen (bricht ab,
    wenn Qt nicht lädt, statt die GUI-Tests zu überspringen).
  - `ruff.toml`: nur Fehlerregeln Pyflakes `F` und `E9`. Funde bereinigt: ungenutzte Importe und f-Strings ohne
    Platzhalter entfernt; Schleifenvariablen, die die Übersetzungsfunktion `_` überdeckten, umbenannt
    (`agent/kidctl_agent/watcher.py`, `win/watcher.py`). Ein Test patcht `mail.send` jetzt direkt statt über
    `accounts.mail`.
  - Agent-E2E-Test an das zweistufige Login (0.39.0) angepasst; der Test schlug seitdem fehl.
  - `dev/openssf-best-practices.md`: Kriterien zu Beiträgen, Tests, CI und Warnungen auf Met; offen nur der
    Branch-Schutz (Einstellungen beschrieben).

  Getestet:
  - Server-, Agent- und Tray-Tests lokal grün; Backup-Tests mit `pg_dump`/`pg_restore` per
    `exec -e PGUSER -e PGPASSWORD -e PGDATABASE` im Container (wie in CI).
  - `ruff check` (0.17.6) ohne Funde, `python dev/check_docs_parity.py` ok, YAML des Workflows gültig.
  - Workflow-Lauf in GitHub erst nach dem Push möglich.
- 2026-11-20: Version 0.44.0 (Server): Design-Durchsicht aller Seiten (Etappe 6) und CSP ohne `'unsafe-inline'` für
  Styles (Issue #3).
  - Neues Skript `dev/design-review.py`: 36 Seiten (Anmeldung beider Stufen, Elternbereich, Verwaltung), mobil und
    Desktop, hell und dunkel, DE/EN; Bilder in `dev/design-review/` (nicht im Repo), Fundliste
    `dev/design-review/FINDINGS.md`.
  - Behoben (zentral in `style.css`): Restzeit bricht nicht mehr um; Status-Chips unter „Zuletzt gesperrt“ mobil nicht
    mehr abgeschnitten; Balkendiagramm passt mobil (letzter Tag, letzter Wert); Abstand zwischen „Weiter“ und „Mit
    Passkey anmelden“; Abstand nach einzelnen Options-Kacheln; verwaiste Trennlinie in der Mail-Vorschau.
  - `passkey.js`: Fehler des Passkey-Vorschlags im Hintergrund (Conditional UI) nur noch in der Konsole statt als
    Browsermeldung unter dem Login-Formular.
  - Alle 12 Inline-Styles aus 8 Templates in Klassen überführt; CSP `style-src 'self'`; Mail- und Berichtsvorschau mit
    Nonce (`mailer.csp_safe`, `middleware.csp`).
  - `dev/demo_seed.py` meldet sich zweistufig an (Passwort, dann Code) – `dev/screenshots.py` lief seit dem
    zweistufigen Login nicht mehr.
  - Zurückgestellt mit Begründung: siehe `FINDINGS.md` (u. a. abgeschnittener Platzhalter der Familiensuche,
    waagerecht scrollende Reiter als Absicht).

  Getestet:
  - Server-Tests 349 grün; neu: keine `style`-Attribute/`<style>` in Nicht-Mail-Templates, CSP ohne `unsafe-inline`
    auf Seiten und statischen Dateien, `mailer.csp_safe` (kein Inline-Style übrig, Nonce an allen `<style>`, Mail-Regeln
    fürs dunkle Schema danach), `pct`/Klassen `p-0`…`p-100`; Mail-Vorschau und Berichtsvorschau senden eine CSP mit
    passender Nonce.
  - `dev/design-review.py` vorher/nachher (DE+EN, je 144 Bilder): nachher keine CSP-Verstöße oder JS-Fehler in der
    Konsole (Chromium), kein waagerechtes Scrollen; Mail-Vorschau im Rahmen sichtbar gestylt (hell und dunkel),
    Balken und Werte im Statistik-Diagramm korrekt.
  - `dev/screenshots.py` neu gelaufen, geänderte Website- und Doku-Bilder aktualisiert.
- 2026-11-20: Agent: Tray-Verbindung robuster. Sendet der Agent an eine Tray-Verbindung, die nicht abnimmt, trennt er
  sie nach 2 s (Linux, `SEND_TIMEOUT`); unter Windows ist die Ausgangs-Warteschlange je Verbindung begrenzt
  (`MAX_QUEUE`), darüber wird getrennt. Der Takt des Agenten läuft damit unabhängig vom Verhalten einzelner
  Tray-Clients weiter. Getestet: Unit-Tests mit hängendem Writer bzw. voller Warteschlange (`test_tray.py`), alle
  Agent-Tests grün; Prüfung in der Test-VM offen.
- 2026-11-20: Rechnungen: Ausstellen, Bezahlt, Storno, Entwurf speichern und löschen sperren die Rechnungszeile
  (`SELECT … FOR UPDATE`) und prüfen den Status auf dem frischen Stand. Gleichzeitige Aktionen auf dieselbe Rechnung
  ergeben damit genau eine Nummer bzw. genau eine Storno-Rechnung; Nummern bleiben lückenlos. Getestet: je 4 Threads
  stellen dieselbe Rechnung aus bzw. stornieren sie (`test_invoices.py`), alle Server-Tests grün.
- 2026-11-20: Server: Agenten-Nachrichten werden im Worker-Thread verarbeitet (`asyncio.to_thread`); die
  Event-Loop sendet nur noch. `record_usage` lädt die Zählerstände eines Berichts mit einer Abfrage statt einer je
  Eintrag. `push.notify_parents` funktioniert auch aus Worker-Threads (eigener Thread statt Event-Loop).
  Getestet: neuer Test prüft Worker-Thread und genau eine `usage_record`-Abfrage je Bericht (`test_policy.py`),
  alle Server-Tests grün. Offen: Lastmessung vorher/nachher; `async`-Routen mit synchroner DB (z. B.
  `invoice_paid`, `child_*` in `routes/admin.py`) unverändert.
- 2026-11-20: Familien-Import: entpackte Größe des Archivs höchstens 20 MB (vorher 200 MB), passend zum
  Upload-Limit von 2 MB. Getestet: Archiv mit 21 MB entpackt wird mit „Die Datei ist zu groß.“ abgelehnt
  (`test_family_export.py`), Export/Import-Tests grün.
- 2026-11-20: Agent: beschädigte oder nicht mehr passende Zustandsdateien (`policy.json`, `usage.json`,
  `chat.json`, `outbox.json` u. a.) verhindern den Start nicht mehr. `store.load` verschiebt sie nach
  `<name>.broken`, warnt im Log und liefert `None`; der Agent startet ohne diesen Stand und holt die Policy vom
  Server. Getestet: Unit-Test mit kaputtem JSON bzw. falschem Schema in allen vier Dateien (`test_agent.py`), alle
  Agent-Tests grün; Prüfung in der Test-VM offen.
- 2026-11-20: Chat und Anfragen begrenzt: Der Agent nimmt je Benutzer höchstens 20 ungesendete Nachrichten/Anfragen
  an (`OUTBOX_MAX_PER_USER`, darüber `TrayError`), der Server speichert je Kind höchstens 30 neue Nachrichten/
  Anfragen in 10 Minuten (`CHILD_MSG_LIMIT`/`CHILD_MSG_WINDOW`, darüber `Ack(ok=False, error="rate_limited")`
  ohne Push). Getestet: `test_tray.py` (21. Nachricht abgelehnt), `test_chat.py` (Grenze bei Chat und
  Verlängerungsanfrage, keine Zustellung), alle Agent- und Server-Tests grün; Prüfung in der Test-VM offen.
- 2026-11-20: Anmeldung: Versuche mit dem Authenticator-Code werden vor der Prüfung atomar verbucht – je Login-Ticket
  per bedingtem `UPDATE … attempts < 5` (`loginticket.claim`), je Konto per Vorab-Eintrag mit anschließender
  Zählung (`security.begin_totp_attempt`, bei Erfolg `totp_attempt_ok`). Gilt für Eltern- und Betreiber-Login
  sowie die frische Bestätigung. Getestet: je 20 gleichzeitige Versuche → genau 5 zugelassen, Konto danach
  gesperrt (`test_login_steps.py`), alle Server-Tests grün.
- 2026-11-20: Agent: Server-Umzug (`relocate`) nur auf `https://`-Adressen (`http://` nur bei mit
  `--insecure-http` gekoppelten Geräten); andere Ziele werden ohne Verbindungsversuch abgelehnt. Die Prüfung von
  `KIDCTL_RELOCATE_TO` im Server bestand schon. Getestet: `test_relocate_paused.py` (http/ftp/ohne Schema abgelehnt,
  keine Probe), alle Agent-Tests grün; Prüfung in der Test-VM offen.
- 2026-11-20: Server: `X-Forwarded-For` zählt nur noch, wenn die Verbindung aus `TRUSTED_PROXIES` kommt (neue
  Env-Variable, Standard: private Netze und Loopback; `*` = alle, nur für Tests). uvicorn wertet Proxy-Header nicht
  mehr selbst aus (`--no-proxy-headers` statt `--forwarded-allow-ips *`), damit `request.client` die echte
  Gegenstelle bleibt. Doku (`server*.md`) ergänzt; Restlücke „gemeinsames Docker-Netz“ siehe
  [Grenzen](docs/limitations.de.md). Getestet: `test_security.py` (Header nur vom vertrauten Proxy), alle
  Server-Tests grün. Offen: Container-Build/Deploy mit Traefik (macht Adrian).
- 2026-11-20: Kopplung: Der Kopplungscode wird atomar eingelöst (bedingtes `UPDATE … used_at IS NULL`); bei
  gleichzeitigen Anfragen mit demselben Code entsteht genau ein Gerät. Getestet: 8 Threads mit demselben Code →
  1 Gerät, 7 × 400 (`test_smoke.py`), alle Server-Tests grün.
- 2026-11-20: Statische Dateien mit Prüfsumme in der URL (`asset()`, `?v=…`) bekommen
  `Cache-Control: public, max-age=31536000, immutable` (in `SecurityMiddleware`, nur bei Status 200). Getestet:
  `test_security.py` (mit/ohne `v=`, fehlende Datei, normale Seite), alle Server-Tests grün.
- 2026-11-20: Zahlungs-Webhook: Der Body wird erst nach der Prüfung des Geheimnisses ausgewertet; ohne Geheimnis
  antwortet der Endpunkt unabhängig vom Inhalt mit 404, mit falschem Geheimnis mit 401. Getestet: `test_billing.py`
  (ungültiger Body ohne/mit falschem Geheimnis), alle Server-Tests grün.
- 2026-11-20: Version 0.44.1 (Agent und Server): Sicherheits- und Robustheitsfixes aus dem Audit vom 2026-11-20
  (Einträge oben), Security-News DE/EN. Funde 10 und 11 (Umbau Profil-Abläufe, Aufteilung `routes/operators.py`)
  bewusst noch offen. Getestet: alle Server-, Agent- und Tray-Tests grün, `ruff check`, Doku-Parität, `hugo --minify`.
- 2026-11-21: Release 0.44.1 veröffentlicht; neun Security Advisories (GHSA-8rhw-3w88-c4q5, GHSA-9jpp-3jrg-6832,
  GHSA-9j39-9rgp-64qj, GHSA-mj6x-2wj9-rfx2, GHSA-p67g-62fj-w22c, GHSA-pc77-rv46-jjjp, GHSA-j2pc-62p2-gg7j,
  GHSA-cxr8-m76j-x35w, GHSA-5cv2-m8xm-h356) veröffentlicht und in der News 0.44.1 verlinkt. Getestet: `hugo --minify
  --panicOnWarning`.
- 2026-11-21: Agent-Tests unabhängig vom Testrechner (ohne neue Version): Der Workflow „Tests“ scheiterte auf Ubuntu
  mit 10 Agent-Tests, weil Sprache (Sitzungsprozesse, AccountsService, `/etc/locale.conf`), Distribution (dpkg/pacman)
  und PAM-Datei vom Rechner kamen. `tests/conftest.py` setzt jetzt Deutsch, Arch und die Arch-PAM-Datei fest;
  `integrity.install_hint` liest die Pfade aus Modulkonstanten. Getestet: Agent-Tests lokal (290) und in einer
  bwrap-Sandbox wie Ubuntu-CI (ohne `locale.conf`-Sprache, dpkg statt pacman, Debian-PAM, `LANG=C`): vorher 10 rot,
  nachher grün; `ruff check` ok.
- 2026-11-21: Projektseite unter eigener Domain `https://kidctl.de/` (ohne neue Version): `baseURL`, Verweise in README,
  Server (Projekt-Link in Mails/Fußzeile, `security.txt`-Policy, Repo-Seite, Fehlermeldung), Release-Skript, Doku-Paritäts-
  Check und Tests umgestellt; alte `adriannoss.github.io/kidCTL/`-Links leitet GitHub weiter. Getestet: Server-Tests (358),
  Doku-Parität, `ruff check`, `hugo --minify --panicOnWarning`; DNS (A/AAAA/www) und Zertifikat geprüft.
- 2026-11-21: Server-Adresse `app.kidctl.de` statt `kids.nossco.de` (ohne neue Version): Traefik-Regel, Standard von
  `BASE_URL`, Beispiele, Web-Scan und Observatory, Kopplungshinweise, `Documentation=` der systemd-Units (jetzt
  Projektseite) und Sicherheitsrichtlinie umgestellt. Neu `docker-compose.legacy-host.yml`: alte Adresse bleibt für
  Agenten (`/api/`), Paketquellen (`/repo/`) und `/.well-known/` erreichbar, der Rest leitet dauerhaft um; Agenten
  wechseln per `KIDCTL_RELOCATE_TO`. Doku „Adresse wechseln“ in `docs/server*.md` (inkl. Passkeys neu anlegen,
  Neuanmeldung, PWA/Push, Paketquellen). GPG-Schlüsselkennung bleibt. Getestet: Server-Tests (358), Doku-Parität,
  `ruff check`, Hugo, YAML der Zusatzdatei; Traefik-Weiterleitung erst beim Deploy prüfbar.
- 2026-11-21: Version 0.44.2 (Server): weitere Schutz-Header aus dem Web-Scan (Issue #3). Jede Antwort trägt
  `Permissions-Policy` (Kamera, Mikrofon, Standort, Zahlung, USB, seriell aus; Passkeys bleiben erlaubt),
  `Referrer-Policy: strict-origin-when-cross-origin`, `Cross-Origin-Opener/Resource-Policy: same-origin` und
  `Cross-Origin-Embedder-Policy: require-corp`; Seiten außer `/static/` und `/repo/` mit `Cache-Control: no-store`.
  Die CSP-Warnung `style-src unsafe-inline` ist seit 0.44.0 behoben. Bewusst offen: ZAP-Hinweise ohne Handlungsbedarf
  (Session-/Login-Erkennung, „Cookie Poisoning“ bei reflektierten Formularwerten, „Non-Storable Content“).
  Getestet: Server-Tests (359, neuer `test_security_headers`), Agent-Versionstest, Design-Review-Skript in Chromium
  über alle 36 Seiten (hell/dunkel, mobil/Desktop) ohne Konsolenfehler oder CSP-/Policy-Verstöße.
- 2026-11-21: Lieferkette: Python-Pakete werden nur noch mit Hash-Prüfung installiert. `server/requirements.in`
  nennt die direkten Abhängigkeiten, `server/requirements.txt` ist daraus mit `pip-compile --generate-hashes`
  erzeugt (alle transitiven Pakete fest); Docker-Image installiert mit `--require-hashes --no-deps`, Basis-Image per
  Digest festgelegt. CI-Pakete in `.github/requirements/{lint,tests,tray}.{in,txt}` ebenso; Dependabot für beide
  Ordner und das Docker-Image ergänzt. Dev-Skript und Doku installieren aus `.github/requirements/tests.txt`.
  Behebt die Scorecard-Einträge „Pinned-Dependencies“. Getestet: Image gebaut und `app.main` importiert, alle drei
  CI-Dateien in frischem Python-3.12-venv mit `--require-hashes` installiert (`pip check` ok), Dry-Run im lokalen
  3.14-venv ok, YAML gültig, Doku-Parität ok.
- 2026-11-21: Docker/Traefik (ohne neue Version): `http://app.kidctl.de` leitet dauerhaft auf https um (Router
  `kidctl-http` auf dem Entrypoint `web` mit `redirectscheme`); bisher antwortete Traefik dort mit 404, was im
  Web-Scan (Observatory „redirection-missing“, Note B+) auffiel. Getestet: YAML gültig; Wirkung erst
  nach dem Deploy prüfbar (`curl -I http://app.kidctl.de/` → 308 auf https, danach Web-Scan erneut).
- 2026-11-21: Agent (in 0.45.0): Verbindung zu https-Servern unter Linux repariert – der Agent übergab `ssl=None`, das
  websockets ab Version 14 bei `wss://` ablehnt (Fehler seit 0.32.4, sichtbar mit `python-websockets` 17.2 auf Arch);
  jetzt wird `ssl` nur mit eigenem Kontext (Windows) übergeben, auch beim Umzugstest. Außerdem werden Tray-Clients, die
  nicht lesen, nach dem Sende-Timeout abgebrochen statt geschlossen (`close` wartete auf den nie geleerten Puffer;
  Befund der VM-Prüfung). Getestet: Agent-Tests (295, neu `test_tls_connect.py` mit echtem `connect` für `wss://`
  und `ws://`, Abbruch-Test im Tray), echter Verbindungsaufbau gegen `wss://app.kidctl.de`; VM-Nachprüfung offen.
- 2026-11-21: Umbau ohne Verhaltensänderung (Audit-Fund 10, ohne neue Version): Profiländerungen von Eltern und
  Betreibern nutzen gemeinsame Bausteine in `server/app/routes/profile_shared.py` (Bestätigung per Passkey,
  Passkeys anlegen/umbenennen/auswählen, Prüfung des neuen Authenticator-Codes, JSON-Antworten); Kontoart,
  Protokoll, Hinweis-Mails und Redirect-Ziele bleiben in den Routen. Getestet: alle Server-Tests grün,
  `ruff check` sauber, Routenliste (Methode, Pfad, Name) vor/nach identisch.
- 2026-11-21: Umbau ohne Verhaltensänderung (Audit-Fund 11, ohne neue Version): `server/app/routes/operators.py`
  (über 1.300 Zeilen) nach Aufgaben aufgeteilt – `operators.py` (Anmeldung, Passkey-Anmeldung, Menü),
  `operator_families.py`, `operator_codes.py`, `operator_log.py`, `operator_profile.py`, `operator_tickets.py`,
  `operator_invoices.py`, `operator_branding.py`, `operator_mails.py` (Mail-Vorschau, Newsletter); gemeinsamer
  Router-Aufbau (Präfix `/admin`, Netzprüfung) und Seiten-Rendering in `operator_base.py`. Rollenprüfung je Route
  unverändert. Getestet: alle Server-Tests grün, `ruff check` sauber, Routenliste (Methode, Pfad, Name) vor/nach
  identisch.
- 2026-11-21: Version 0.45.0 – Rechnungskopf mit Erscheinungsbild umgesetzt (`invoices.render_pdf`): Logo und Name
  des Dienstes oben links über dem Absenderblock; Absenderangaben unverändert. Wirkt nur auf neu ausgestellte
  Rechnungen, gespeicherte PDFs bleiben unangetastet (keine Migration). `dev/sample_invoices.py` erzeugt zusätzlich
  `musterrechnung-logo.pdf`. Getestet: neuer Test (Standard nur Name ohne Bild; mit Logo und eigenem Namen Text und
  eingebettetes Bild im PDF; zuvor ausgestellte Rechnung behält ihr PDF byte-gleich nach Branding-Änderung), volle
  Testsuite, Muster-PDFs als Bild geprüft (quadratisches Logo und Grenzfall 4:1-Logo mit überlangem Namen: nichts
  überdeckt).
- 2026-11-21: VM-Prüfung der Audit-Fixes 1, 5, 6, 8 (ohne neue Version) mit dem Agenten von main (0.44.2, Paket in
  der Arch-VM gebaut, gekoppelt mit dem Dev-Server, Kind-Konto mit Sitzung). Neue Hilfsskripte
  `dev/vm/traytest.py` (Tray-Socket als Kind: Zustand, nicht lesende Verbindung, Nachrichtenflut) und
  `dev/vm/relocatetest.py` (Umzugsnachricht gegen den installierten Agenten). Ergebnis: Fund 1 – Takt und Zählung
  laufen weiter, kein Neustart; Fund 5 – beschädigte `usage`/`policy`/`apptime`/`outbox` werden nach `.broken`
  verschoben und gemeldet, Agent startet und setzt durch; Fund 6 – Begrenzung im Agenten (20 offen) und im Server
  (30/10 min) greift online wie offline, Outbox wird nach Wiederverbindung abgearbeitet; Fund 8 – Umzug auf `http://`
  wird abgelehnt (Parser und Agent), Gerät bleibt beim alten Server. Zwei Auffälligkeiten intern gemeldet (Nachbesserung
  offen, Belege: die beiden Skripte).
- 2026-11-21: VM-Nachprüfung 0.45.0 (ohne neue Version; Paket von main in der Arch-VM gebaut). Tray-Client, der nicht
  liest (`dev/vm/traytest.py stall 90` als Kind): nach 17 s getrennt, 2 Log-Warnungen statt Dauer-Spam, Zählung und
  Policy laufen weiter, kein Neustart. Linux-TLS: in der VM ein TLS-Endpunkt `https://localhost:8443` (selbst
  signiert, im System vertraut, weitergeleitet an den Dev-Server), Dev-Server mit `KIDCTL_RELOCATE_TO` → Agent zieht
  um und verbindet sich per `wss://` (Gerät bleibt anerkannt); `dev/vm/relocatetest.py` grün (http-Ziel und unbekanntes
  https-Ziel abgelehnt, keine Ausnahme). Danach VM-Gerät auf den Dev-Server zurückgesetzt.
- 2026-11-21: Planung der nächsten drei Etappen (Funktionsrecherche, mit Adrian abgestimmt): Abschnitt „Geplante
  Funktionen“ – 0.46 Freigabe anfragen, Ferien/Feiertage, mehrere Zeitfenster (parallel Lutris/Bottles); 0.47 Limit
  je Programm/Kategorie, „zählt nicht“-Programme, Zeitkonto; 0.48 Aufgaben → Zeit verdienen, Weich-Modus, Alarmkanäle
  ntfy/Webhook/Matrix, Cinnamon/COSMIC. Plan in `dev/planung/PLAN-2026-11.md`, Roadmap der Website ergänzt. Keine
  Umsetzung; geprüft: Doku-Parität und Hugo-Build.
- 2026-11-22: Geräte entfernen (in 0.45.1) (`POST /devices/{id}/delete`, Knopf „Gerät entfernen“ in der Gerätedetailansicht).
  Migration 0024: `usage_record.device_id`/`app_usage.device_id` nullable mit `ON DELETE SET NULL`, damit die
  Statistik je Kind bleibt; „Nutzung auf anderen Geräten“ zählt Zeilen ohne Gerät mit. Doku zur Deinstallation
  (DE/EN) nennt nun „Gerät entfernen“. Geprüft: Tests (Löschen inkl. CSRF, fremde Familie → 404, verbundenes Gerät
  wird mit `CLOSE_REVOKED` getrennt, altes Token abgewiesen, Statistik bleibt; Kind löschen mit zugeordnetem Gerät
  → Policy-Push ohne Zuordnung), komplette Suite gegen PostgreSQL, `alembic check`, Down-/Upgrade, Upgrade einer
  0023-Datenbank mit Daten, Browser-Smoke-Test im Dev-Server.
- 2026-11-22: Agent (Linux/Windows, in 0.45.1): Bei neuer Zuordnung eines Geräts bzw. Users zu einem Kind kam
  der ganze Chatverlauf als Benachrichtigungsflut (nur die erste Nachricht des Abgleichs war still, weil „hat Verlauf“
  je Nachricht geprüft wurde). Jetzt merkt sich der Agent je Kind Zeitpunkt und Art der letzten `chat_sync`-Anfrage;
  ältere Nachrichten sind Verlauf (erster Abgleich still, sonst eine Sammelmeldung nach 2 s Ruhe). Die erste
  Live-Nachricht an ein Kind ohne Verlauf benachrichtigt jetzt ebenfalls. Außerdem kamen Entscheidungen, die fielen,
  während der Rechner offline war, nie an (`chat_sync` holte nur höhere IDs); jetzt fragt der Agent ab der ältesten
  offenen Anfrage nach (`ChatStore.sync_after`), die Entscheidung kommt mit normaler Benachrichtigung. Getestet: Agent-Unittests
  (`test_chat_notify.py`: erster Abgleich still, danach live; erste Live-Nachricht ohne Verlauf; Offline-Sammelmeldung;
  einzelne nachgeholte Nachricht; offline gefallene Entscheidung), Agent-Testsuite grün; e2e-Tests mangels `httpx` lokal nicht gelaufen, nicht in der
  VM geprüft. (News-Ergänzung mit 0.45.1 zurückgenommen.)
- 2026-11-22: Fehler behoben (Windows): Hearthstone über Battle.net erzeugte drei Sperrdialoge trotz freigegebenem
  Battle.net (`Hearthstone Beta Launcher.exe`, `ProgramData\Battle.net\Agent\Agent.exe`, `…\Agent.9886\Agent.exe`).
  Ursache: Der Katalog-Schlüssel ist das Startmenü-Ziel `Battle.net Launcher.exe`, Spieleordner und Abstammungsregel
  erwarteten `Battle.net.exe`; der Agent liegt außerhalb des Programmordners. Gleiches Muster bei EA app
  (`EALauncher.exe`) und Ubisoft Connect (`UbisoftConnect.exe`); Steam, Epic, GOG, Wargaming zeigen direkt auf den
  Launcher. Startmenü-Ziele, Agent-Ordner und Rechte in der Windows-VM (Snapshot „launchers“) nachgesehen;
  Agent-Unittests mit den echten Pfaden (294 grün, e2e-Tests mangels Server-Umgebung nicht gelaufen). Nicht live
  geprüft: Start von Hearthstone über Battle.net (Spiel in der VM nicht vollständig installiert). Für 0.45.1
  (0.45.0 ist ohne diesen Fix erschienen; Versionssprung gesammelt).
- 2026-11-22: Fehler (Windows): Spielzeit eines über Ubisoft Connect gestarteten Spiels („Starlink“) lief unter
  „Ubisoft Connect“. Ursache, Teil 1: Startmenü-Einträge der Launcher (`Battle.net Launcher.exe`, `EALauncher.exe`,
  `UbisoftConnect.exe`) galten nicht als Launcher (`win/agent.is_launcher`), zählten also als Spiel – und alles im
  Programmordner (bei Ubisoft auch der Spieleordner, falls darunter installiert) lief unter diesem Eintrag. Behoben.
  Teil 2: Ubisoft installiert Spiele unter `…\Ubisoft Game Launcher\games`, und Programme im Ordner eines laufenden
  Launchers sind vom Abstammungs-Rückfall ausgenommen; Starlink wird zudem von `UbisoftGameLauncher.exe` gestartet,
  nicht von `upc.exe`. Jetzt erkennt der Agent Ubisoft-Spiele aus der Registry (`Ubisoft\Launcher\Installs`,
  `game:ubisoft:<id>`) und gibt ihre Ordner mit Ubisoft Connect frei. Format aus Registry-Auszug und Prozessliste von
  Adrians Rechner (Testdaten anonymisiert in `win_launchers/registry.json`). EA, GOG, Battle.net erkennen Spiele
  bereits aus Registry/Datei, auch unterhalb des Launcher-Ordners (Index vor Katalog). Agent-Unittests 296 grün; nicht
  live geprüft. Für 0.45.1.
- 2026-11-22: Fehler (Windows): Für Minecraft musste `gamingservicesui.exe` aus
  `WindowsApps\Microsoft.GamingServices_<Version>_x64__8wekyb3d8bbwe` einzeln freigegeben werden (Freigabe hing an der
  Versionsnummer). Ursache: Der Prozess läuft ohne App-Identität, wurde also als normale `.exe` außerhalb jeder
  Freigabe gesperrt. Jetzt: Programme unter `WindowsApps` ohne Identität zählen zu ihrem Paket; Gaming Services sind
  Basis wie die Game Bar. Ältere Einzelfreigaben gelten weiter. Unittest mit dem echten Pfad (Agent 297 grün); nicht
  live geprüft. Für 0.45.1.
- 2026-11-22: Fehler (Windows): Minecraft: Java Edition erschien nur als „Minecraft Launcher“, und die Java-Laufzeit
  (`AppData\Local\Packages\Microsoft.4297127D64EC6_8wekyb3d8bbwe\LocalCache\…\bin\javaw.exe`, vom Launcher ins
  Profil geladen, ohne App-Identität, Pfad mit Laufzeit-Version) musste einzeln freigegeben werden. Jetzt gehört
  `java[w].exe` aus den Paketdaten des Minecraft Launchers zum Paket (Freigabe bzw. Anfrage gilt dem Launcher) und
  zählt als Spiel `game:minecraft:java` „Minecraft: Java Edition“; der Launcher selbst zählt nicht mehr als Spiel.
  Bewusst eng (nur dieses Paket, nur Java) – Restlücke in `docs/limitations*.md`. Pfad vom echten Rechner; Unittest
  (Agent 298 grün); nicht live geprüft. Für 0.45.1.
- 2026-11-22: Fehler (Windows): Minecraft Bedrock hatte keine Steuerung, bis `C:\Program Files\Microsoft GameInput\x64\
  GameInputRawInputProxy.exe` einzeln freigegeben war. Microsoft GameInput gehört jetzt zur Basis (Ordner unter
  `Program Files`, für Kinder nicht beschreibbar). Unittest mit dem echten Pfad; nicht live geprüft. Für 0.45.1.
- 2026-11-22: Kontaktadressen auf die Projekt-Domain umgestellt (ohne neue Version): Sicherheitsmeldungen
  `security@kidctl.de` (SECURITY*, README*, `security.txt` der Website und Server-Route, `.env.example`), Impressum
  `info@kidctl.de`, Maintainer der Debian-Pakete `KidCTL <info@kidctl.de>` statt `kidctl@localhost`. Geprüft:
  Doku-Parität, Hugo-Build (keine alte Adresse in der Ausgabe); Server-Tests nicht gelaufen (kein psycopg lokal).
- 2026-11-22: Version 0.45.1 – Sammel-Patch der Fixes vom 2026-11-22 (Geräte entfernen, keine Chat-Benachrichtigungsflut
  und nachgeholte Offline-Entscheidungen, Windows-Spiele/Launcher-Freigaben inkl. Ubisoft-Spielerkennung, Minecraft,
  Gaming Services, GameInput, Kontaktadressen auf kidctl.de). v0.45.0 war bereits veröffentlicht; Ergänzungen der
  News 0.45 zurückgenommen (Patch ohne Sicherheitsbezug, keine eigene News). Geprüft: Server-Suite gegen PostgreSQL,
  Agent-Tests, Doku-Parität, Hugo-Build.
- 2026-11-22: Version 0.45.1 (Server): Programmliste und Rechnungsaktionen nachgebessert (Rückmeldung Adrian zu
  Bildern aus dem echten Betrieb).
  - Programmliste: Jede App ist eine Kachel, „Spiel“ und „Entfernen“ stehen unter dem Namen statt daneben. Vorher
    quetschten sie den Namen und lange Pfade eigener Programme auf wenige Buchstaben je Zeile, und angehakte Spiele
    machten ihre Kachel schmaler als die übrigen.
  - Als Spiel markierte Apps (auch eigene Programme) stehen in der Gruppe „Spiele“ (`programs.grouped_catalog`).
  - Knöpfe neben Feldern mit Label darüber (`.row.bottom`, `form.row.inline`) sitzen mittig zum Feld statt an
    dessen Unterkante (Rechnung: „Als bezahlt markieren“, „Stornieren“, „Erneut senden“).
  - `dev/design-review.py` legt zusätzlich eigene Programme mit langen Windows-Pfaden an; `dev/design-review.py` und
    `dev/screenshots.py` nehmen den Container aus `KIDCTL_PG_CONTAINER` (wie die Tests).
  - Doku `webui*.md`: Kästchen „Spiel“ unter der App, markierte Apps in „Spiele“.

  Getestet:
  - Server-Tests 361 grün, neu `test_games_listed_under_games` (markierte und eigene Apps unter „Spiele“, nicht mehr
    installierte bleiben dort, eigene ohne Markierung unter „Eigene Programme“).
  - `dev/design-review.py --only child-programs` (DE, hell/dunkel, mobil/Desktop): Kacheln mit langen Pfaden lesbar,
    keine Konsolenfehler, kein waagerechtes Scrollen.
  - Rechnungsaktionen mit der echten `style.css` in Chromium nachgebaut: Datumsfeld und alle drei Knöpfe auf
    derselben Mittellinie (gemessen).
  - Wegen fehlender Kernel-Module (`tun`) nach Kernel-Update lief PostgreSQL in einem temporären Container
    `kidctl-pg-tmp` im Host-Netz (`KIDCTL_PG_CONTAINER=kidctl-pg-tmp`).
- 2026-11-22: Version 0.45.2 (Tray Windows): Fenster und Dialoge der Tray-App zeigen in Titel- und Taskleiste das
  KidCTL-Symbol statt des generischen Windows-Programmsymbols. `winstyle.apply` setzt einmal `app.setWindowIcon`
  (alle Fenster erben es); Quelle ist `kidctl.ico` der Installation (`{app}\kidctl.ico`), sonst das gezeichnete Logo
  (wie im Toast). Linux unverändert (Symbol über die Desktop-Datei). Getestet: neuer Test (Rückfall-Logo, ICO der
  Installation, Dialog erbt das Symbol), Tray-Tests grün; in der Windows-11-VM (Code per `winsync.sh`) als Kind
  angemeldet: Chatfenster und „Verlängerung anfordern“ zeigen das Symbol in Titelleiste und Taskleiste, hell und
  dunkel; `dev/winshots.py` (DE, hell/dunkel) ohne Auffälligkeiten.
- 2026-11-22: Fehler (Webfilter, Linux und Windows): Eine eigene Sperre bzw. Sperrliste griff nicht für Namen,
  die SafeSearch per CNAME umschreibt (z. B. `youtube.com`/`www.youtube.com` gesperrt, YouTube trotzdem
  erreichbar). SafeSearch-Zonen nur noch für nicht gesperrte bzw. freigegebene Namen; Sperrzonen in fester
  Reihenfolge. Getestet: Agent-Unittests (neu: Sperre schlägt SafeSearch, Freigabe hebt sie auf), 305 grün;
  Nachstellung in der VM steht noch aus.
- 2026-11-22: Version 0.45.2 (Agent, Server, Tray): fasst drei bisher getrennte Branches zusammen – Design-Korrekturen
  (Programmliste als Kacheln, Spiele unter „Spiele“, Rechnungsknöpfe; zuvor als „0.45.1“ geführt, nicht
  veröffentlicht), KidCTL-Symbol in der Windows-Tray-App und die korrigierte Rangfolge der Webfilter-Zonen
  (Sicherheitsfix). Security-News DE/EN, Advisory-Entwurf. Getestet: siehe Einträge oben; zusammen Server-, Agent- und
  Tray-Tests, `ruff check`, Doku-Parität, Hugo. VM-Prüfung des Webfilter-Fixes noch offen.
- 2026-11-22: Projektseite: Der Pages-Build beim Release wartet bis zu 2 min, bis der neue Release in der GitHub-Liste
  steht (bei 0.45.1 und 0.45.2 fehlte er sonst auf der Releases-Seite). Getestet: YAML geprüft, manueller Neubau.
- 2026-11-22: Update per Knopf (Abschnitt „Verteilung & Updates“): `hello.features`, `update_now`/`update_status`,
  Route `/devices/{id}/update`, Migration 0025, Agent Linux (pacman nur `[kidctl]` bzw. apt nur `kidctl.sources`, als
  transiente Unit `kidctl-update`) und Windows (Selbst-Updater sofort). Doku `updates`/`webui` DE/EN, README, Website.
  Keine Version/News (kommt mit der Info-Seite in 0.46.0). Getestet: Server-pytest (neu `test_selfupdate.py`,
  Fremdfamilie in `test_tenant.py`, i18n), Agent-Tests (315, gemockter Paketmanager, Windows `run(force=True)`),
  `ruff`, Doku-Parität, Hugo. **Offen:** VM-Smoke-Test Arch/Debian/Windows, Screenshots, `alembic check`.
- 2026-11-22: Info und Neuigkeiten: Info-Abschnitt in den Einstellungen und unter `/admin/info` (Version,
  Release-Datum, Protokollversion, Links, Whitelabel „basiert auf KidCTL“), tägliche Versionsabfrage bei
  `kidctl.de/version.json` (`UPDATE_CHECK`), einmaliger Hinweis „Neu in Version …“ auf der Übersicht
  (`parent.seen_version`, Migration 0026), Tray-Menüpunkt „Info“ (Linux/Windows), `version.json` auf der
  Projektseite. Getestet: `server/tests/test_info.py` (Login-Pflicht, beide Admin-Rollen, Banner DE/EN bis
  „Verstanden“, CSRF, Whitelabel, Versionsabfrage mit Fehlerfällen, Version = Agent) und gesamte Server-Suite gegen
  PostgreSQL; Migration gegen Alt-Schema-Datenbank (0024 → head, `alembic check`, Downgrade); Tray-Tests inkl.
  Info-Dialog, Dialog offscreen in Linux- und Windows-Optik angesehen; Hugo-Build mit Beispiel-Releases.
- 2026-11-22: Projekthomepage mobil: Burger-Menü oben (unter 840 px) mit Funktionen, Anleitungen, Roadmap, News, Releases, GitHub
  und Sprachwahl; `<details>` ohne JS bedienbar, `menu.js` schließt es bei Link/Esc/Klick daneben. Doppelte Linkliste in der
  Fußzeile auf Mobil ausgeblendet (Impressum/Datenschutz/RSS bleiben). Desktop unverändert. Getestet: `hugo --minify
  --panicOnWarning` fehlerfrei; Browser-Pane bei 375 px (Doku, News; hell/dunkel) und Desktop-Breite.
- 2026-11-22: Version 0.46.0 – Update per Knopf je Gerät, Info-Seite mit Versionsanzeige und „Was ist neu“ (WebUI,
  Tray), Burger-Menü der Projektseite; News 0.46 (DE/EN), Roadmap ergänzt. Zielversionen der geplanten Etappen um eins
  verschoben (0.47–0.49). Beim Zusammenführen Migration 0026 auf 0025 umgehängt. Geprüft: Server-Suite gegen
  PostgreSQL, Agent- und Tray-Tests, ein Migrationskopf (0026), Doku-Parität, Hugo-Build.
- 2026-11-23: Linux-Programmkontrolle (Gegenstück zum Windows-Fix aus 0.32.6): `noexec` auf Kinder-Home und Spieleordnern gilt nur noch im Modus „Sperren“ (Entscheidung
  des Nutzers, vorher „≠ Aus“): Bei „Nur protokollieren“ konnte das Kind Programme aus dem Home nicht starten, obwohl der Modus
  nichts sperren soll. Desktop-Verknüpfungen werden ebenfalls nur noch bei „Sperren“ an root übergeben. `/tmp`, `/var/tmp`, `/dev/shm`
  bleiben unabhängig vom Modus `noexec` (bekannte Grenze, siehe Programm-Whitelist). Beim Wechsel zu „Nur protokollieren“ während
  einer laufenden Sitzung wird das Home erst nach der Abmeldung ausgehängt (wie bisher). Getestet: Agent-Tests grün (3 Fälle neu
  bzw. erweitert, nach Rebase auf 0.46.0 erneut). Am 2026-11-08 auf Stand 0.32.5: neue Arch-VM mit gebautem Paket, als `kind` ohne angemeldete Sitzung, Wechsel Protokollieren → Sperren → Protokollieren:
  bei „Nur protokollieren“ kein Mount, Programm aus Home und Spieleordner (`/srv/steam/kind`, Steam als leerer Ordner `/usr/lib/steam`
  simuliert) startet und steht als „nur protokolliert“ im WebUI; bei „Sperren“ beide `noexec`, Start „Permission denied“; zurück: sofort
  ausgehängt, Start wieder möglich; `/tmp` in allen Modi `noexec`. Nicht getestet: echte Plasma-Sitzung, echtes Steam.
- 2026-11-23: Version 0.46.1 – noexec nur im Modus „Sperren“ (Linux), Dependabot-Updates (u. a. fastapi 0.165, SQLAlchemy
  2.1.9, reportlab 5.1, pyotp 2.12). Patch ohne Sicherheitsbezug, keine News. Geprüft: Server-Suite gegen PostgreSQL
  (inkl. Rechnungs-PDFs mit reportlab 5), Agent- und Tray-Tests, Doku-Parität, Hugo-Build.
- 2026-11-23: Chat-Rate-Limit sichtbar: Verwirft der Server eine Nachricht/Anfrage des Kindes wegen des Rate-Limits
  (`Ack(ok=False, error=ACK_RATE_LIMITED)`, Konstante neu in `common/protocol.py`, Feld unverändert – ältere Agenten
  verwerfen weiter still), schickt der Agent der Tray-App des Absenders ein `TrayError` in dessen Sprache („Zu viele
  Nachrichten in kurzer Zeit – deine letzte Nachricht wurde nicht zugestellt …“); die Tray-App (Linux und Windows,
  gemeinsamer Code) zeigt es als Hinweis. Andere Ablehnungen bleiben still. Getestet: `test_tray.py` (Hinweis nur bei
  `rate_limited` und bekannter Nachricht, vorher rot), Tray-`test_ui.py` (Hinweis aus dem Socket wird angezeigt),
  nach Rebase auf 0.46.1 mit Bibliotheken aus `.github/requirements/tests.txt`: alle Agent-Tests (318, E2E mit eigener Datenbank), Tray-Tests (29, System-Python mit PySide6), Server-Suite gegen PostgreSQL (378).
- 2026-11-23: K1 „Freigabe anfragen“ (Etappe 1) als erledigt abgeschlossen, ohne Code: Die Funktion besteht seit
  0.24.0 (Freigabe-Anfragen, 2026-10-20, vom Nutzer auf dem Kinderrechner getestet) und deckt den Planeintrag ab
  (Tray-Liste und Sperr-Benachrichtigung Linux/Windows, Grund, Dauerhaft/Nur heute/Ablehnen, Push). Abschnitt
  „Geplante Funktionen“, `dev/planung/PLAN-2026-11.md` und Roadmap (`request-access` → done, 0.24) angepasst.
  Geprüft: Doku-Parität, Hugo-Build.
- 2026-11-23: Lutris und Bottles als Spielequellen unter Linux (Roadmap `wine-launchers` → in Arbeit; Abschnitt
  „Spielzeit je Spiel“): `games.py` erkennt Lutris (`GAME_NAME` + `LUTRIS_GAME_UUID`) und Bottles (`BOTTLE`,
  Programmliste der `bottle.yml`), Bottles-/Lutris-Flatpak als Launcher. Formate aus echten Installationen in der
  Test-VM (Lutris 0.5.22 Arch-Paket mit Wine 11.22; Bottles 67.7 Flatpak mit soda-11.0-10), anonymisierte Testdaten
  unter `agent/tests/fixtures/wine_launchers`. Doku Spiele/Restlücken/WebUI (DE/EN), README. Getestet: neue
  Agent-Tests (`test_wine_launchers.py`: echte Umgebungen und `bottle.yml`, Umlaute/umbrochene Pfade, fremder
  Besitzer, Symlink, Hilfsprozesse, Agent-Zählung „spielt gerade“/Programmzeit), komplette Agent-Suite (323 grün),
  ruff, Doku-Parität. VM-Smoke: Erkennungsfunktionen aus dem Repo als root gegen laufende Prozesse – Lutris-Spiel und
  seine Wine-Prozesse → „Test-Spiel: Wüste“, Bottles-Programm → „Wüsten-Rennen“, Bottles-Hilfsprozesse ohne Spiel.
  Nicht in der VM geprüft: kompletter Agent mit Server (Übersicht/Statistik, Spiele-Limit), Plasma-Sitzung statt
  Xvfb, Lutris mit Proton/umu, Bottles nativ (nicht Flatpak), Programmkontrolle „Sperren“.
- 2026-11-23: Ferien und Feiertage (Etappe 1, K2; per /grill-me entschieden, Abschnitt „Bildschirmzeit → Ferien und
  Feiertage“): Ferienzeiträume je Familie und Feiertagsregion DE/AT/CH (Einstellungen → „Ferien & Feiertage“),
  Ferienvorlage je Kind (Tab „Zeiten“, mit Sondertagen der nächsten 14 Tage), Feiertage lokal per `holidays` (0.109,
  gepinnt) und „gilt wie“-Wochentag. Ohne Protokolländerung: Policy enthält die geltenden Regeln von heute … heute+6,
  Job nach Mitternacht sendet geänderte Policies an verbundene Geräte. Migration 0027 (`vacation`, `vacation_rule`,
  `holiday_region`), Export/Import enthält die neuen Tabellen. Restlücke >6 Tage offline in `docs/limitations*.md`.
  Getestet: `server/tests/test_vacations.py` (Feiertage je Region, geltende Regel inkl. Feiertag in Ferien,
  rollierendes 7-Tage-Fenster, Zeitzone/Mitternacht und Sommerzeit, Policy unverändert ohne Ferien, Tageswechsel-Job
  sendet nur geänderte Policies, Gutschrift gegen Ferienregel, WebUI mit CSRF, fremde Familie, Löschen mit Kind,
  Export/Import) und gesamte Server-Suite gegen PostgreSQL; Migration gegen Alt-Schema-Datenbank (0026 mit Daten →
  head, `alembic check`, Downgrade/Upgrade); Doku-Parität, Hugo-Build, Screenshots „Zeiten“, Design-Review (Zeiten,
  Heute, Einstellungen; keine Konsolen-/Layoutfunde).
- 2026-11-23: Sperrzeiten und Zeitfenster über Mitternacht (Roadmap `time-windows`, Etappe 1; /grill-me mit Adrian,
  siehe „Bildschirmzeit → Sperrzeiten und Fenster über Mitternacht“). Je Tag bis zu 3 Sperrzeiten mit Namen
  (`weekday_rule.blocks`, verschlüsselt, Migration `0027_sperrzeiten`), Ende < Beginn = über Mitternacht (bis 06:00, auch
  für Gutschriften). Protokoll 2 (`DayRule.blocks`), Server bedient Protokoll 1 weiter mit abgespeckter Policy (ohne
  Sperrzeiten, Ende auf 23:59 gekappt) und Hinweis auf der Seite „Zeiten“. Agent (Linux und Windows, gemeinsamer Kern
  `rules.py`): erlaubte Abschnitte inkl. Rest des Vortagsrahmens, Grund `blocked` ohne „Verlängerung anfordern“;
  Tray zeigt „Sperrzeit „Schule“ bis 13:00 Uhr“, die Sperrzeiten des Tages und das Ende des laufenden Abschnitts.
  Geprüft: pytest Server (Formular, Validierung, Policy v1/v2, Gutschrift über Mitternacht, Hinweis), Agent-Unittests
  (Sperrzeiten, Mitternacht, Zeitumstellung März/Oktober, Offline mit gecachter Policy, Benachrichtigung), Tray-Texte,
  Migration 0026 → head → 0026 → head gegen PostgreSQL mit `alembic check`, Doku-Parität, Hugo-Build, Screenshots und
  Design-Review der Seite „Zeiten“ (mobil/Desktop, ohne Funde). Offen: VM-Test Linux und Windows (Warnung, Abmeldung,
  Login-Sperre in einer Sperrzeit, Fenster über Mitternacht, Tray-Anzeige).
- 2026-11-23: Version 0.47.0 (Vorgänger 0.46.1) – Etappe 1 zusammengeführt: Ferien und Feiertage (K2), Sperrzeiten
  und Fenster über Mitternacht (K3, Protokoll 2), Lutris/Bottles, Chat-Limit-Hinweis; K1 bestand bereits seit 0.24.
  Beim Zusammenführen: Migration der Sperrzeiten als 0028 hinter 0027 (Ferien/Feiertage) gehängt und legt die Spalte
  `blocks` auch für `vacation_rule` an – die Ferienvorlage hat eigene Sperrzeiten (Formular unter Kind → Zeiten →
  Ferienvorlage; noch nicht gespeicherte Ferienvorlage ist mit der Wochenvorlage samt Sperrzeiten vorbelegt). Policy
  liefert die Sperrzeiten der jeweils geltenden Regel (Woche, Ferien oder Feiertag). Minor-Version → News. Geprüft:
  Server-Suite gegen PostgreSQL inkl. neuem Test Ferienvorlage mit Sperrzeiten in der Policy (Protokoll 2), Agent-
  und Tray-Tests, Doku-Parität, Hugo-Build, Screenshots. VM-Tests am 2026-11-23 gegen den Entwicklungsserver
  (Migration 0024 → 0028 beim Start): Arch-VM mit gebautem Paket 0.47.0 (`kidctl`, `-plasma`, `-gnome`), Prüfung
  per `pamtest.py login kind kind` – Sperrzeit 22:30–23:30 im Fenster 08:00–01:00 → abgelehnt (Policy über
  Protokoll 2 mit `blocks`), ohne Sperrzeit erlaubt; VM-Uhr Di 00:30 → erlaubt (Fenster vom Montag reicht über
  Mitternacht), 01:05 → abgelehnt; Ferienzeitraum heute mit Ferienvorlage bis 22:00 → abgelehnt, Policy Mo/Di aus
  der Ferienvorlage, Rest Wochenvorlage; Ferienvorlage bis 23:59 mit/ohne Sperrzeit 22:30–23:30 → abgelehnt/erlaubt;
  `traytest.py flood` als `kind`: nach 30 Nachrichten Hinweis „Zu viele Nachrichten in kurzer Zeit …“ statt stillem
  Verwerfen. Windows-11-VM (Code per `winsync.sh`, Agent 0.47.0): Sperrzeit → Konto `kind` deaktiviert, ohne
  Sperrzeit wieder aktiviert; Uhr Di 00:30 → frei, 01:05 → gesperrt; Ferienvorlage bis 22:00 hält die Sperre, bis
  23:59 hebt sie auf. Sitzungstest (angemeldet per QMP; Arch-VM ohne GL, sonst liefert `screendump` kein Bild):
  Plasma als `kind`, Sperrzeit „Abendessen“ ab +2 min – Tray-App zeigt „Sperrzeiten: Abendessen …“ und „Noch 1 min“,
  ab Beginn „Sperrzeit ‚Abendessen‘ bis …“; Benachrichtigung „Noch 1 Minute – danach wirst du abgemeldet“, Abmeldung
  nach 60 s, neuer Login am SDDM mit Sperrhinweis abgelehnt. Lutris-Prozess (`wine64-preloader` mit `GAME_NAME`/
  `LUTRIS_GAME_UUID`) in der Sitzung → WebUI „spielt gerade: VM-Test: Wüste“. Windows 11 als `kind` angemeldet:
  Countdown-Dialog und Benachrichtigung vor Beginn, „Bildschirmzeit zu Ende – Abmeldung in 60 s“, Abmeldung,
  Konto deaktiviert. Nicht geprüft: echtes Lutris/Bottles mit Wine-Spiel (nur nachgestellte Umgebung).
- 2026-11-24: Entwicklungsablauf mit Testinstanz (mit Adrian entschieden): neue Entwicklung auf Branch `dev`, Test auf
  `dev.kidctl.de` (eigener Ordner und Stack auf srv02, eigene Schlüssel und Testdaten, Aktualisierung manuell durch
  Adrian), nach `main` nur nach Adrians OK; Sicherheitsfixes direkt auf `main`. `docker-compose.yml` mit
  `KIDCTL_NAME`/`KIDCTL_HOST` für eine zweite Instanz (Standardwerte = bisherige Namen, Prod unverändert),
  `tests.yml` auch für Pushes auf `dev`, Anleitung `dev/dev-instanz.md`, Variablen in `docs/server*.md` und
  `.env.example`. Offen: eigener Paketkanal für dev (Vorab-Versionen, Update-Knopf auf Testgeräten). Geprüft:
  Compose-Ergebnis für Prod und dev per Variablen-Ersetzung verglichen (Prod identisch), Doku-Parität.
- 2026-11-24: Paketkanal der Testinstanz (mit Adrian entschieden): Testgeräte an dev.kidctl.de bekommen Vorab-Versionen
  aus `dev` über das Repo der Testinstanz und den Update-Knopf. Schema `<nächste Minor>dev<Commit-Anzahl>`
  (`0.48.0dev312`, Debian `0.48.0~dev312`, Windows-Dateiversion `0.47.65535.312`), kleiner als das Release; neuer
  gemeinsamer Vergleich `kidctl_common.version` in Server (`versions.py`: Repo-Erkennung, Update-Hinweis) und
  Windows-Updater (Manifest erlaubt `devN`). Bau per `prerelease.yml` (Push auf `dev` bzw. von Hand, ohne Tag/Release,
  ohne SignPath), Version nur im Bau (`packaging/devversion.py`). Veröffentlichung `kidctl-dev-release.sh` mit
  denselben Schlüsseln nach `repo-dev`/`publish-target-dev`; `--dev` in `kidctl-repo-build.sh`, `kidctl-repo-publish.sh`,
  `build.py`. Windows-Agenten bis 0.47.0 lehnen `devN`-Manifeste ab – erstes Update eines Windows-Testgeräts von Hand.
  Prod unverändert (`release.yml` und Release-Aufruf von ISCC identisch, Versionen ohne `dev` sortieren wie
  bisher). Geprüft: Tests für Vergleich/Erkennung/Manifest/Installer-Sortierung, `vercmp` gegen pacman,
  Veröffentlichung nie aufs Prod-Ziel (Fake-rsync); lokaler `makepkg`- und `build-deb.py`-Bau mit `0.48.0dev293`
  (Paketnamen, Abhängigkeiten `kidctl=0.48.0dev293` bzw. `0.48.0~dev293-1`). Nicht geprüft: Windows-Bau mit
  Vorab-Version (Inno Setup, erst im ersten Lauf von `prerelease.yml`), Signatur/Veröffentlichung (Adrian) – beides am
  2026-11-26 nachgeholt, siehe dort; dpkg-Vergleich (dpkg lokal nicht installiert).
- 2026-11-24: Ersteinrichtung: `python -m app.cli master-key` und `vapid-keys` brechen nicht mehr ab, solange `.env`
  noch unvollständig ist (bisher verlangte schon das Laden der CLI `KIDCTL_MASTER_KEY` – der dokumentierte Befehl ging
  bei einer Neuinstallation nicht; aufgefallen beim Einrichten von dev.kidctl.de). Beide Befehle laufen jetzt vor den
  App-Importen. Geprüft: Test ruft die CLI ohne Konfiguration auf (Schlüssel 32 Byte, VAPID-Paar), andere Befehle
  verlangen die Konfiguration weiter.
- 2026-11-25: Kleinkram 0.48 (WebUI). **Gerät koppeln:** Gibt es einen Windows-Installer im Repo, verlinkt die Seite
  direkt den neuesten („Installer herunterladen (Version)“); die Download-Seite bleibt als Rückfall bzw. für ältere
  Versionen und öffnet in neuem Tab. Linux verweist auf die Anleitung „Kinderrechner (Linux)“ (Paketquelle +
  Paket, neuer Tab). **Kind → Zeiten:** Sperrzeiten in Wochen- und Ferienvorlage einklappbar (`<details>`, ohne
  JavaScript); eingeklappt, solange keine eingetragen ist, aufgeklappt mit Anzahl, sobald welche bestehen oder beim
  Speichern der jeweiligen Vorlage ein Fehler auftritt. **Einstellungen:** Knöpfe neben Feldern mit Beschriftung
  (Familie, Ferien, Feiertage, Support-Zugriff, Export/Import, Familie löschen, Backup; Verwaltung: Rechnung
  erstellen) sitzen mittig zum Feld statt zur Beschriftung – über die zentrale Klasse `.row.bottom`; Formulare mit
  Text hinter dem Feld (Gerät: Alarm, Chat: Anfrage anpassen) unverändert. **Eigene Domains:** höchstens 1 000 je
  Feld (freigegeben/gesperrt, Konstante `webfilter.MAX_CUSTOM_DOMAINS`, kein `.env`-Wert, weil die 4-MB-Grenze des
  Agenten fest ist); Prüfung nur beim Speichern mit Fehlermeldung DE/EN. Ältere, längere Listen bleiben speicherbar,
  dürfen aber nicht wachsen. Geprüft: pytest (Installer-Link mit/ohne Installer, Sperrzeiten zu/auf/Fehler, Limit
  inkl. Altbestand, Übersetzungen), Design-Review vorher/nachher (Einstellungen, Zeiten, Gerät, Chat,
  Verwaltung Familie/Preisliste, hell/dunkel, mobil/Desktop), Screenshots `child-times`, `pair`, `game-limit` neu.
- 2026-11-25: „Zählt nicht“-Programme (K5, Anforderungen über die Planungssitzung mit Adrian abgestimmt, siehe Abschnitt
  „„Zählt nicht“-Programme“): Kästchen „Zählt nicht“ im Programmkatalog (nicht bei Spielen, Browsern, Terminals,
  Dateimanagern, Entwicklungswerkzeugen, Launchern), Tabelle `child_app_rule` (Alembic `0029`), Policy-Feld
  `free_apps` ohne neue Protokollversion, Hinweis auf Geräte ohne Fähigkeit `free_apps`. Agent Linux/Windows: Takt
  zählt nicht, solange nur ausgenommene Programme laufen; Umgehungsschutz (root-eigene Datei, Name/Versionsinfo,
  Flatpak-Namensraum, geschützte Windows-Ordner), laufender Browser zählt immer. Tray-Zeile „Die Zeit läuft gerade
  nicht“. Edge-Schnellstart und Browser-Hintergrundmodus per Policy aus. Doku `webui`, `limitations` (neue Restlücke),
  README, Funktionsliste, Roadmap `free-apps` → in Arbeit. Getestet: Server-Suite gegen PostgreSQL (412), Agent (362
  + 3 E2E), Tray (31), darunter Umgehungsversuche (umbenannte Browser-Binary im App-Ordner bzw. als `lernapp.exe`
  mit `OriginalFilename` chrome.exe, gefälschte Flatpak-Cgroup, Kopie im Home, Programm im Benutzerordner, Policy mit
  Browser/Spiel/Konsole); Migration gegen Alt-Schema-Datenbank (0028 → head, `alembic check`, Downgrade/Upgrade);
  Doku-Parität, `hugo --minify --panicOnWarning`, Design-Review „Programme“ DE/EN ohne Auffälligkeiten,
  Screenshots erneuert. Nicht geprüft: VM Linux (KDE) und Windows mit echten Prozessen.
- 2026-11-25: Zeitkonto (K7, Etappe 2): nicht genutzte Minuten teilweise auf Folgetage, mit Anteil, Deckel je Tag,
  Kontogrenze 3 × Deckel und Verfall (siehe „Bildschirmzeit → Zeitkonto“).
  - Server rechnet zustandslos (`app/timebank.py`); Migration `0030_zeitkonto` (Spalten `child.bank_*`).
  - Guthaben als zusätzliche Tagesausnahme in der Policy, also ohne Protokolländerung.
  - Nachmeldungen vergangener Tage schieben geänderte Policies nach.
  - WebUI-Karte im Tab *Zeiten*, Zeile im Tab *Heute*; Statistik und Bericht rechnen das Guthaben ein.
  - Doku `webui`, README und Funktionsliste ergänzt, Restlücke eingetragen, Roadmap `time-bank` auf „in Arbeit“.
  - Getestet:
    - `server/tests/test_timebank.py`: Einzahlung/Verbrauch/Deckel/älteste zuerst, Verfall und Kontogrenze,
      Gutschriften zählen nicht, Jahreswechsel mit Ferienvorlage, Formular samt Fehlerfällen und Ausschalten,
      Policy mit Guthaben, Nachmeldung schickt neue Policy, Policy ohne Zeitkonto unverändert.
    - Gesamte Server-Suite gegen PostgreSQL (416 grün).
    - Agent-Unittest: mehrere Ausnahmen eines Tages werden addiert.
    - Design-Review *Zeiten*/*Heute* (DE/EN, hell/dunkel) ohne Auffälligkeiten.
    - Migration gegen eine Alt-Schema-Datenbank (0028 mit Kind → head → 0028 → head, `alembic check` ohne
      Änderungen).
- 2026-11-25: Limit je Programm/Kategorie (K4, Etappe 2, siehe Abschnitt „Limit je Programm/Kategorie“).
  - Server: Karte im Tab *Zeiten*, Zeilen im Tab *Heute*, Policy-Feld `app_limits`, Zeit je Limit geräteübergreifend
    (Migration `0031_app_limits`). Agent Linux/Windows: Zählung je Limit, Vorwarnung und Schließen (`AppLimit`),
    Fähigkeit `app_limits`; Tray-Zeilen. Demo-Daten: Browser-Limit für Max, *Musik & Video* für Lena.
  - Doku `webui`, `limitations` (neue Restlücke), README, Funktionsliste, Roadmap `app-limits` → in Arbeit.
  - Getestet:
    - `server/tests/test_app_limits.py`: setzen/ändern/entfernen, Fehleingaben ohne Teilspeicherung, Höchstzahl,
      gemeinsame Zeile mit „zählt nicht“, Zeit je Limit über zwei Geräte (Maximum, Weitermeldung), alter Agent ohne
      Feld, englische Karte, Hinweis auf Geräte ohne Fähigkeit; gesamte Server-Suite gegen PostgreSQL grün.
    - `agent/tests/test_app_limits.py`: Zuordnung (Kategorie, Name ohne Katalog, Steam, Windows-Schreibweise),
      parallel geöffnete Browser zählen einmal, „zählt nicht“ + Limit, Vorwarnung/SIGTERM/SIGKILL nur für passende
      Prozesse, nächster Tag frei, Offline-Speicherung und andere Geräte, Tray/Bericht, englische Texte,
      Windows-Agent mit Edge + portablem Chrome; gesamte Agent-Suite und Tray-Tests grün.
    - Migration gegen Alt-Schema-Datenbank (0030 mit Regel und Nutzung → head → 0030 → head, `alembic check` ohne
      Änderungen); Doku-Parität, `hugo --minify`; Design-Review *Heute*/*Zeiten* (DE/EN, hell/dunkel) ohne
      Auffälligkeiten; Screenshots *child-today* erneuert.
    - Nicht geprüft: VM Linux (KDE) und Windows mit echten Prozessen und Benachrichtigungen.
- 2026-11-26: Nutzer-Test (Adrian) der 0.48-Vorabversion unter Windows auf echter Hardware, erfolgreich:
  - Paketkanal der Testinstanz: Installer aus `prerelease.yml`, signiert und veröffentlicht per
    `kidctl-dev-release.sh`, Installation und Update aus dem Repo von dev.kidctl.de.
  - Limit je Programm/Kategorie (K4), „Zählt nicht“-Programme (K5), Zeitkonto (K7).
  - Battle.net-Spielerkennung (offener Livetest seit 0.36.0) – damit erledigt.
  - Grundfunktionen: Bildschirmzeit, Sperre, Website-Filter, Tray.
- 2026-11-26: Programme auf Wechseldatenträgern nicht freigebbar (Befund 1 aus
  `dev/planung/2026-11-26-usb-datentraeger-recherche.md`, von Adrian in 0.48 vorgezogen; siehe Programmkontrolle
  „Wechseldatenträger“). Linux `programs.on_removable` (udisks2-Einhängepunkte) und `Allowed.path_ok`, Windows
  `win/procs.on_removable` (Laufwerkstyp/Bus, Cache 10 s) und `classify(removable=…)`: Pfad- und Ordnerfreigaben
  gelten dort nicht, Sperre mit Code `removable_media` ohne „Freigabe anfragen“; Store-Apps und Launcher-Bibliotheken
  bleiben erlaubt. Server: Begründung übersetzt, eigene Programme unter `/run/media/`, `/media/` abgelehnt.
  Restlücke „USB-Sticks und externe Datenträger“ in `docs/limitations*`. Getestet: Unittests Linux/Windows
  (Pfad- und Ordnerfreigabe, Skript vom Stick, eigene Datei, Steam-Bibliothek und Store-App auf USB-Platte, Launcher
  auf dem Stick, Erkennung inkl. Cache/Systemlaufwerk/Fehler/Deskriptor), komplette Suiten Server (429) und Agent
  (379) grün, ruff sauber. VM-Test am selben Tag (Agent aus dem Arbeitsbaum, Dev-Server, per QMP eingesteckte
  USB-Geräte): **Windows 11 Home** – Erkennung über die echte API: C: (SATA) nein, USB-Stick (Removable), USB-Platte
  (meldet sich als „Fixed“, Bus USB), eingehängtes ISO (CD-ROM) ja, Netzpfad nein. Kind im Modus „Sperren“ mit
  Freigaben für `E:\…` und `D:\…`: Programme beendet, Protokoll „Wechseldatenträger“ (WebUI DE) bzw. „removable
  drive“ (EN), Toast „… ist nicht freigegeben“ ohne „Freigabe anfragen“. **Arch/Plasma** – Stick und USB-Platte über
  die Geräteanzeige als Kind eingehängt (`/run/media/kind/…`, ausführbar): freigegebene Programme auf beiden und ein
  Skript vom Stick beendet („Wechseldatenträger“). Nicht in der VM: `/media/` (Debian/Ubuntu), Steam-Bibliothek und
  Store-App auf USB-Platte (nur Unittests). VMs danach auf den Snapshot `vor-usb` zurückgesetzt.
- 2026-11-26: Nutzer-Test (Adrian) der 0.48-Vorabversion unter Linux auf echter Hardware erfolgreich.
- 2026-11-26: Version 0.48.0 (Etappe 2): Limit je Programm/Kategorie (K4), „Zählt nicht“-Programme (K5), Zeitkonto
  (K7), Kleinkram 0.48, Programme auf Wechseldatenträgern nicht freigebbar. Release Notes, News, Roadmap `app-limits`,
  `free-apps`, `time-bank` → erledigt (0.48). Geprüft: VM Linux/Windows (K4, USB-Fix), Nutzer-Tests Linux und Windows
  auf echter Hardware (Vorabversion), Server-, Agent- und Tray-Tests, Doku-Parität, Hugo-Build.
- 2026-11-26: Nintendo-Switch-Spielzeit (experimentell, Phase 1 nur lesen), siehe Abschnitt
  „Nintendo-Switch-Spielzeit“. Getestet: `server/tests/test_nintendo.py` mit Attrappen der Bibliothek, ohne echte
  Nintendo-Aufrufe (Abbildung inkl. „kein Limit“ und unvollständiger neuer Konsole, Erfolg, Auth-Fehler → „reauth“
  ohne Retry, Netzfehler → stale mit Backoff und Erholung, Fehler einer Konsole, `_fetch` gegen Bibliotheks-Attrappen,
  Einrichtung über die WebUI inkl. Verifier, Token verschlüsselt in der DB und nicht im Export, falscher, abgelehnter
  und abgelaufener Link, Zuordnung, Heute und Statistik, Trennen behält Statistik, fremde IDs 404, Schalter aus,
  SaaS-Standard aus, Mindestintervall); keine Tokens oder Nicknames im Log. Ganze Server-Suite grün. Migration 0031 →
  0032 auf einer 0031-Datenbank, `alembic check`, Downgrade/Upgrade. Nicht getestet: Abruf mit echtem
  Nintendo-Konto (Adrian, `dev/nintendo_smoke.py`), deutsche Beschriftung der Nintendo-Seite („Diese Person auswählen“)
  nicht verifiziert.
- 2026-11-26: Feature vorschlagen (Teil B, siehe Abschnitt „Feature vorschlagen“): Seite `/feature-request`, Mail an
  `FEATURE_REQUEST_EMAIL` im Format `KIDCTL-FEATURE-REQUEST v1`, Rate-Limit 5/Tag je Elternteil, Doku DE/EN.
  Getestet: `tests/test_feature_requests.py` (aus ohne Empfänger, Login-Pflicht, CSRF, Validierung und Bereinigung,
  Mailformat per Regex, Kontakt nur mit Häkchen inkl. Reply-To, Rate-Limit und Freigabe nach 24 h, Fehlversand zählt
  nicht, englische Oberfläche mit deutschem Mailformat), Mailtyp in DE/EN über `test_mails.py`, Seite im Browser
  (Formular, Fehler, Bestätigung) mit gemocktem SMTP.
- 2026-11-26: Feature-Inbox (Arbeitspaket Teil C, Abschnitt „Feature-Inbox“): Dienst `tools/feature-inbox/` (IMAP →
  v1-Parser → Aufbereitung mit `claude-haiku-5-5` → GitHub-Issue mit `feature-request`/`needs-triage`, Tagesübersicht
  per Mail), Compose-Service im Profil `feature-inbox`, Sperre der Issue-Automatik über `dev/issues_freigegeben.py`
  (Zeitplan-Task angepasst), CI-Schritt und Dependabot. Getestet: 19 Tests mit gemocktem IMAP/GitHub/Modell/SMTP
  (Parser, Idempotenz inkl. Absturz nach dem Anlegen, Unsortiert, GitHub-Fehler, Probelauf, Duplikate,
  Modellausfall/Refusal, Prompt-Injection mit böswilliger Mail und böswilliger Modellantwort, Tagesübersicht,
  IMAP-PEEK/MOVE-Ersatz), 3 Tests der Sperre, ruff; Image mit `podman build` gebaut, startet und meldet fehlende
  Einstellungen; `anthropic` 2.14.0 im Image kennt `output_config`. Nicht getestet: echtes Postfach, GitHub und
  Claude-API (Einrichtung macht Adrian), `docker compose config` (lokal kein Compose).
- 2026-11-26: USB-Datenträger – Grundsatz von Adrian entschieden: alle USB-Massenspeicher (Sticks, Festplatten) für
  Kinder gesperrt, nur von Eltern erlaubte Geräte (Hardware-Kennung) zugreifbar, Linux und Windows. Neuer Abschnitt
  „USB-Datenträger“, Recherche-Dokument (Entscheidungen, offene Fragen) und Roadmap `usb-media` (geplant)
  nachgezogen. Noch nicht umgesetzt.
- 2026-11-26: USB-Datenträger – Details entschieden: erlaubte Geräte nur für Dateien (Programme weiter gesperrt), Liste
  je Familie, Schalter je Kind (neue Kinder gesperrt, bestehende erlaubt), mitgesperrt Abbilder, CD/DVD, SD-Karten,
  Handys (MTP). Spec-Abschnitt und Recherche nachgezogen.
- 2026-11-26: Projektseite blieb nach dem Release 0.48.0 auf 0.47 stehen: GitHub Pages verwirft still eine Bereitstellung
  mit derselben Build-Version (Commit-SHA), die der Push des Release-Commits schon deployt hat (actions/deploy-pages#383).
  `pages.yml` startet sich beim Release-Ereignis jetzt per `workflow_dispatch` neu (Tag als Eingabe für die Warteschleife).
  Geprüft: Ursache über Artefakt (version.json = 0.48.0) vs. Live-Seite (0.47.0, Last-Modified vom Push-Lauf); Workflow-YAML
  geparst. Wirkung erst beim nächsten Release prüfbar.
- 2026-11-26: Nintendo-Einrichtung: Button „Neuen Link erzeugen“ unter dem Einfügefeld. Vorher war ein schon
  verbrauchter Login-Link bis zum Ablauf (30 min) nicht ersetzbar. Getestet: abgelehnter Link → neuer Link ersetzt
  URL und Verifier, Einstellungen zeigen den neuen Link.
- 2026-11-27: Planung (Adrian): Gehostete Variante (`saas`) gilt als erledigt, Adrian testet sie im Betrieb; Roadmap
  `saas` → done. `FEATURE_REQUEST_EMAIL=features@kidctl.de` auf app.kidctl.de eingerichtet. Version 0.49 = Nintendo-
  Switch-Spielzeit (experimentell) + Feature vorschlagen/Feature-Inbox, Release nach Adrians Test auf dev.kidctl.de
  (Roadmap `nintendo-switch`, `feature-requests` → Version 0.49). Version 0.50 = USB-Datenträger, K6 Aufgaben,
  K13 Weich-Modus; K10, `desktops` und eigene Sperrlisten danach. Nur Planung/Roadmap, kein Code.
