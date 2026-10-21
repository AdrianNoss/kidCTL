# Installation

Vollständige Einrichtung von KidCTL. Die Reihenfolge ist wichtig, weil jeder Teil auf dem vorherigen
aufbaut:

1. **Server**: Eltern-WebUI und Agent-API (Docker hinter Traefik)
2. **Paket-Repository**: signierte Pakete für die Kinderrechner, Pacman und APT (auf dem eigenen Rechner gebaut)
3. **Kinderrechner**: Paket installieren, koppeln, Benutzer zuordnen
4. **Einstellungen** im WebUI
5. **Härtung** gegen physischen Zugriff

In den Beispielen steht `kids.example.org` für die eigene Subdomain.

## Voraussetzungen

| Komponente | Voraussetzung |
|---|---|
| Server | Linux mit Docker + Docker Compose, laufender **Traefik** mit certresolver `letsencrypt` und Middleware `secHeaders@file`, DNS-Eintrag für die Subdomain |
| Bau-Rechner | Arch Linux mit `base-devel`, `git`, `gnupg`, `rsync`, SSH-Zugang zum Server |
| Kinderrechner | **Arch Linux** mit KDE Plasma (SDDM) und/oder GNOME (GDM), jeweils unter Wayland, oder **Debian 13, Ubuntu 24.04/26.04 und darauf aufbauend** (Kubuntu, Linux Mint, Pop!_OS) mit GNOME oder Plasma auf 64-Bit-PCs (amd64); Internetzugang, ein Admin-Account mit `sudo` |
| Eltern | Smartphone mit Authenticator-App (TOTP, z. B. Aegis, Google Authenticator) |

## 1. Server

### 1.1 Code holen und Datenordner anlegen

```bash
git clone https://github.com/AdrianNoss/kidCTL.git /srv/kidctl
cd /srv/kidctl
mkdir -p data
sudo chown 10001:10001 data     # der Container läuft als Benutzer 10001
```

`./data` enthält die SQLite-Datenbank, Sperrlisten und das Paket-Repository (Pacman und APT). **Den Ordner extern
sichern.**

### 1.2 Konfiguration (`.env`)

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # Ausgabe als SECRET_KEY eintragen
```

| Variable | Bedeutung |
|---|---|
| `SECRET_KEY` | Pflicht. **Nie ändern**, sonst sind alle Sessions und Geräte-Token ungültig (Geräte neu koppeln). |
| `BASE_URL` | Öffentliche Adresse, z. B. `https://kids.example.org` |
| `TOTP_ISSUER` | Name in der Authenticator-App (Standard `KidCTL`) |
| `TZ_HOME` | Zeitzone für Tageskontingente und Zeitfenster (Standard `Europe/Berlin`) |
| `VAPID_*` | Web Push, siehe 1.4. Ohne Schlüssel gibt es keine Push-Benachrichtigungen. |
| `SMTP_*` | E-Mail für „Passwort vergessen“, Sicherheitshinweise und Nutzungsbericht (optional), siehe 1.7 |

### 1.3 Traefik und Start

In `docker-compose.yml` anpassen:

- `Host(\`kids.nossco.de\`)` → eigene Subdomain
- Netzwerkname unter `networks.traefik.name` und `traefik.docker.network` → tatsächlicher Name
  (`docker network ls`)

```bash
docker compose up -d --build
docker compose logs -f kidctl      # „Application startup complete“
```

Test: `https://kids.example.org` zeigt die Anmeldeseite.

### 1.4 Web Push (empfohlen)

Push braucht man für Chat-Nachrichten, Verlängerungsanfragen und Alarme auf dem Handy.

```bash
docker compose run --rm kidctl python -m app.cli vapid-keys
```

Die Ausgabe in `.env` eintragen, `VAPID_SUBJECT=mailto:<eigene Adresse>` setzen, dann
`docker compose up -d`.

### 1.5 Ersten Eltern-Account anlegen

```bash
docker compose exec kidctl python -m app.cli create-parent
```

Benutzername und Passwort eingeben, dann den angezeigten QR-Code mit der Authenticator-App
scannen. Weitere Eltern-Accounts legt man danach im WebUI unter „Eltern“ an.

### 1.6 Handy einrichten

1. `https://kids.example.org` im Handy-Browser öffnen und anmelden.
2. „Zum Startbildschirm hinzufügen“ bzw. „App installieren“ (PWA). Unter iOS funktioniert Push nur
   in der installierten App.
3. In der App unter „Benachrichtigungen“ Push aktivieren und „Test“ drücken.

### 1.7 E-Mail und Konto-Wiederherstellung (empfohlen)

In `.env` (danach `docker compose up -d`):

| Variable | Bedeutung |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | Mailserver, z. B. Port 587 |
| `SMTP_SECURITY` | `starttls` (Port 587), `ssl` (Port 465) oder `none` (nur im lokalen Netz) |
| `SMTP_USER`, `SMTP_PASSWORD` | Zugangsdaten, leer lassen ohne Anmeldung |
| `SMTP_FROM` | Absender, z. B. `KidCTL <kidctl@example.org>` |

Jedes Elternteil trägt unter **Profil** (Klick auf den eigenen Namen oben rechts) seine E-Mail-Adresse
ein und wählt dort die Sprache (Automatisch nach Browser, Deutsch oder English) für WebUI, Mails und
Push. Dort lassen sich auch Benutzername, Anzeigename, Passwort und der Authenticator ändern.

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

## 2. Paket-Repository (Pacman und APT)

Die Pakete werden **auf dem eigenen Rechner** (Arch) gebaut und signiert – die Arch-Pakete und, ohne
`dpkg`, auch die `.deb`-Pakete samt APT-Repository unter `repo/deb`, der private Schlüssel kommt nie
auf den Server. Alle Skripte laufen ohne `--apply` nur als Probelauf. Details:
[`packaging/repo/README.md`](../packaging/repo/README.md).

### 2.1 Signierschlüssel (einmalig)

```bash
packaging/repo/kidctl-repo-key.sh create --apply
```

- Passphrase vergeben. Am Ende zeigt das Skript den **Fingerabdruck**, den man für jeden
  Kinderrechner braucht.
- Name/Mail des Schlüssels über `KIDCTL_GPG_UID="Name <mail>"` änderbar.
- **Privaten Schlüssel sichern** (Passwortmanager). Ohne ihn akzeptieren die Kinderrechner keine
  Updates mehr:

  ```bash
  GNUPGHOME=~/.local/share/kidctl-repo/gnupg gpg --armor --export-secret-keys > kidctl-repo-secret.asc
  ```

### 2.2 Bauen und veröffentlichen

```bash
packaging/repo/kidctl-repo-build.sh --apply
KIDCTL_REPO_TARGET=user@server:/srv/kidctl/data/repo/ packaging/repo/kidctl-repo-publish.sh --apply
```

Der SSH-Benutzer braucht Schreibrechte auf `data/repo`, und die Dateien müssen für den Container
lesbar sein (z. B. `chown <user> data/repo`, Rechte `755`/`644`). Test:
`https://kids.example.org/repo/kidctl.db` und
`https://kids.example.org/repo/deb/dists/stable/InRelease` lassen sich herunterladen.

Für die `.deb`-Pakete lädt der Bau beim ersten Mal die mitgebrachten Python-Bibliotheken für Ubuntu
24.04 (`pydantic`, `websockets`, Liste mit Prüfsummen in `packaging/debian/vendor.txt`) nach
`~/.cache/kidctl-vendor`.

## 3. Kinderrechner

Alles als Admin auf dem Kinderrechner, sofern nicht anders angegeben.

### 3.1 Kinder-Accounts

Jedes Kind bekommt einen **normalen** Account, also nicht in `wheel`:

```bash
sudo useradd -m -c "Max" max
sudo passwd max
```

Wichtig: Accounts in `wheel`, `sudo` oder `admin` und root schränkt KidCTL grundsätzlich nie ein.
So kann man sich als Admin nicht selbst aussperren.

### 3.2 Repository einrichten und Paket installieren

```bash
curl -O https://kids.example.org/repo/kidctl-repo-client.sh
sudo bash kidctl-repo-client.sh https://kids.example.org/repo <FINGERABDRUCK>           # Probelauf
sudo bash kidctl-repo-client.sh https://kids.example.org/repo <FINGERABDRUCK> --apply
# Arch
sudo pacman -S kidctl kidctl-plasma   # KDE Plasma
sudo pacman -S kidctl kidctl-gnome    # GNOME
# Debian, Ubuntu, Mint, Pop!_OS
sudo apt install kidctl kidctl-gnome  # GNOME (Ubuntu, Debian, Pop!_OS 22.04)
sudo apt install kidctl kidctl-plasma # KDE Plasma (Kubuntu)
```

Das Skript erkennt die Distribution: Unter Arch trägt es `[kidctl]` in `/etc/pacman.conf` ein, unter
Debian/Ubuntu legt es den Schlüssel nach `/etc/apt/keyrings/kidctl.asc` und das Repository nach
`/etc/apt/sources.list.d/kidctl.sources`. Updates kommen danach mit dem normalen Systemupdate
(auch über die automatischen Updates von Ubuntu).

**Debian/Ubuntu – Besonderheiten:**

- Die Login-Sperre trägt sich per `pam-auth-update` ein (Profil „kidctl“ in `/etc/pam.d/common-account`).
- Ubuntu 24.04 und Systeme darauf (Mint 22, Pop!_OS) haben kein PySide6 und nur `pydantic` 1: Die
  Tray-App nutzt dann PyQt6 aus den Systempaketen, der Agent bringt `pydantic` 2 und `websockets` in
  `/usr/lib/kidctl/vendor` mit. Neuere Systeme (Debian 13, Ubuntu 26.04) nutzen ihre eigenen Pakete.
- **Snap-Apps** (unter Ubuntu z. B. Firefox) erscheinen im App-Katalog und werden je Snap-Name
  freigegeben, wie Flatpak-Apps je App-ID.
- **Dateien** (Nautilus) ist unter Ubuntu immer erlaubt: Die Desktop-Symbole von Ubuntu brauchen es
  laufend im Hintergrund.
- **Steam** aus Ubuntus Paket `steam-installer` (`/usr/games/steam`) legt seine Bibliothek wie unter Arch
  unter `/srv/steam/<kind>` ab (`~/.steam/debian-installation` ist ein Verweis dorthin).

Sind beide Desktops installiert, beide Pakete installieren. Das Skript bricht ab, wenn der Schlüssel
nicht zum Fingerabdruck passt. Bei der Installation trägt `kidctl` die Login-Sperre in
`/etc/pam.d/system-login` ein (Sicherung `system-login.kidctl.bak`), stellt `/etc/nsswitch.conf` auf
den DNS-Filter um und aktiviert die Browser-Policies gegen DNS-over-HTTPS.

| Paket | Inhalt |
|---|---|
| `kidctl` | Agent, Tray-App, `kidctl`-Befehl, Login-Sperre, Website-Filter |
| `kidctl-plasma` | SDDM-Theme mit dem Hinweis der Login-Sperre |
| `kidctl-gnome` | AppIndicator-Erweiterung (Tray-Symbol in der oberen Leiste, für alle User voreingestellt), Eintrag „KidCTL“ in der Anwendungsübersicht |

Fehlt zu einem installierten Desktop das passende Paket, weist `pacman` darauf hin und das WebUI
meldet es unter „Integrität“.

**GNOME:** Keine automatische Anmeldung (`AutomaticLogin` in `/etc/gdm/custom.conf`) für Kinder
einrichten. Lehnt die Login-Sperre sie beim Start ab, zeigt GDM keinen Anmeldebildschirm, sondern
bleibt schwarz (Verhalten von GDM). Das WebUI meldet eine solche Einstellung unter „Integrität“.
Schaltet ein Kind die Erweiterung „AppIndicator“ ab, fehlt nur das Tray-Symbol – das WebUI meldet
das, Restzeit und Chat bleiben über „KidCTL“ in der Anwendungsübersicht erreichbar. Wieder
einschalten als Kind: `gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com`.

Ohne Repository (z. B. zum Ausprobieren) geht es auch direkt aus dem Git-Checkout:
`cd packaging/arch && makepkg -si`.

### 3.3 Gerät koppeln

1. WebUI → „Geräte“ → „Gerät hinzufügen“ mit Namen, z. B. „Laptop Max“. Angezeigt wird ein
   Kopplungscode (8 Zeichen, 10 Minuten gültig).
2. Auf dem Kinderrechner:

   ```bash
   sudo kidctl enroll https://kids.example.org <KOPPLUNGSCODE>
   sudo systemctl enable --now kidctl-agent.service
   ```

3. Im WebUI erscheint das Gerät als „online“.

### 3.4 Kind anlegen und zuordnen

1. WebUI → „Kinder“ → Kind anlegen.
2. WebUI → Gerät → „Benutzer-Zuordnung“: die gemeldeten Linux-Accounts dem Kind zuordnen. Nicht zugeordnete Accounts bleiben
   uneingeschränkt.
3. Den Kinderrechner neu starten, damit alle Schutzmaßnahmen greifen (noexec-Home, Tray-App im
   Autostart, Anmeldebildschirm).

### 3.5 Prüfen

```bash
sudo kidctl status                       # Policy und heutige Nutzung
systemctl status kidctl-agent
journalctl -u kidctl-agent -b            # Fehlersuche
```

Im WebUI zeigt der Abschnitt „Integrität“ beim Gerät, ob alle Schutzmaßnahmen aktiv sind. Fällt
später eine aus, kommt ein Push-Alarm.

## 4. Einstellungen im WebUI

Je Kind (Seite des Kindes):

- **Bildschirmzeit**: Wochenvorlage mit Tageskontingent und erlaubtem Zeitfenster (Werktage und
  Wochenende oder jeder Tag einzeln).
- **Spiele-Limit** („davon Spiele“ in der Wochenvorlage): höchstens so viele Minuten Spiele am Tag
  (Steam, Heroic und Programme der Gruppe „Spiele“, nicht die Launcher). Vorwarnung 5 und 1 Minute
  vorher, danach werden Spiele geschlossen; der restliche Rechner bleibt nutzbar. Leer = kein Limit.
- **Zeit gutschreiben**: zusätzliche Minuten, ein längeres Zeitfenster oder zusätzliche Spielzeit für
  ein bestimmtes Datum. Verlängerungs- und Spielzeit-Anfragen aus der Tray-App lassen sich auch direkt
  im Chat genehmigen.
- **Programme**:
  - **Aus**
  - **Nur protokollieren**: zum Einstieg empfohlen, um zu sehen, was genutzt wird.
  - **Sperren**: nur freigegebene Programme. Der Katalog füllt sich mit dem, was der Rechner
    meldet. Steam wird nur als Ganzes freigegeben.
- **Internet** (Website-Filter):
  - **Aus**
  - **Whitelist**: nur Freigegebenes. Dafür gibt es Bundles wie YouTube, Wikipedia, Lern-Apps oder
    Microsoft 365.
  - **Blacklist**: Sperrlisten, z. B. HaGeZi Multi + NSFW, plus eigene Sperren und Ausnahmen.
  - SafeSearch je Kind.
- **Statistik**: Nutzung der letzten 14 Tage und Zeit pro Programm.
- **Freigabe-Anfragen**: Wird ein Programm gesperrt, hat die Meldung beim Kind den Knopf
  „Freigabe anfragen“. Gesperrte Websites fragt das Kind über das Tray-Menü „Gesperrt – Freigabe
  anfragen …“ an. Die Anfrage erscheint im Chat (mit Push) mit **Dauerhaft erlauben**, **Nur heute**
  (gilt bis Mitternacht) oder **Ablehnen**. Dauerhafte Freigaben stehen danach in der Programm- bzw.
  Website-Liste und lassen sich dort zurücknehmen.

Je Gerät: Alarm, wenn das Gerät länger als X Stunden offline ist (Standard 48 h).

### Steam

Die Steam-Bibliothek eines Kindes liegt unter `/srv/steam/<kind>`, `~/.local/share/Steam` ist nur
ein Verweis dorthin. Grund: Bei aktiver Programmkontrolle ist das Home `noexec`, aus dem Home startet
Steam dann nicht. Den Verweis legt der Agent für jedes zugeordnete Kind an. Einen schon vorhandenen
Steam-Ordner im Home verschiebt er nach der Abmeldung des Kindes automatisch.

Das klappt nur, wenn `/home` und `/srv` auf demselben Dateisystem liegen (bei btrfs: auf demselben
Subvolume, bei CachyOS mit `@home` also **nicht**) und die Programmkontrolle für das Kind gerade aus
ist (das noexec-Home ist ein eigener Mount). Sonst meldet das WebUI unter „Integrität“ „…bitte von Hand
verschieben“. Dann von Hand verschieben, bei abgemeldetem Kind und mit beendetem Steam:

```bash
sudo rmdir /srv/steam/<kind>                         # muss leer sein
sudo mv /home/<kind>/.local/share/Steam /srv/steam/<kind>
sudo chown -R <kind>: /srv/steam/<kind> && sudo chmod 700 /srv/steam/<kind>
sudo systemctl restart kidctl-agent                  # legt den Verweis an
```

Der Ordner kann sehr groß sein, das Kopieren zwischen Partitionen dauert entsprechend.

### Heroic (GOG, Epic, Amazon)

Freigegeben wird nur „Heroic“. Electron, die Hilfsprogramme (`gogdl`, `legendary`) und alles, was
Heroic startet (Wine, Proton, Spiele), sind damit erlaubt. Wie bei Steam liegen die Ordner, aus
denen etwas gestartet wird, außerhalb des Homes unter `/srv/heroic/<kind>`:

| Im Home (Verweis) | Ziel |
|---|---|
| `~/Games/Heroic` | `/srv/heroic/<kind>/Games` (Spiele, Wine-Prefixe) |
| `~/.config/heroic/tools` | `/srv/heroic/<kind>/tools` (Wine, Proton) |
| `~/.local/share/umu` | `/srv/heroic/<kind>/umu` (Laufzeit für Proton) |
| `~/.cache/heroic_gogdl`, `~/.cache/legendary` | `/srv/heroic/<kind>/cache/…` (native Module von GOG- und Epic-Anbindung) |

Vorhandene Ordner verschiebt der Agent nach der Abmeldung, soweit möglich (Einschränkungen wie bei
Steam), sonst von Hand wie bei Steam (`/srv/heroic/<kind>/Games` bzw. `tools` als Ziel). Die
Cache-Ordner von `gogdl`/`legendary` werden einfach gelöscht und neu angelegt.

Ist Steam bzw. Heroic für ein Kind mit Programmkontrolle **nicht** freigegeben, hängt der Agent dessen
Spieleordner `noexec` ein. Sonst könnte das Kind dort eigene Bibliotheken ablegen und in erlaubte
Programme laden. Nach der Freigabe wird das sofort aufgehoben. In Heroic den Standard-Installationsordner nicht ändern: Spiele außerhalb von
`~/Games/Heroic` starten bei aktiver Programmkontrolle nicht. Amazon-Spiele (`nile`) funktionieren
bei aktiver Programmkontrolle nicht.

## 5. Härtung

Ohne Härtung lässt sich jede Software-Sperre per USB-Stick oder Bootmenü umgehen. Mindestens
root sperren und Firmware- sowie Bootloader-Passwort setzen: [`docs/haertung.md`](haertung.md).

## Updates

| Was | Wie |
|---|---|
| Server | `git pull && docker compose up -d --build`. Datenbank-Migrationen laufen beim Start automatisch. |
| Client-Paket | `pkgver` in `packaging/arch/PKGBUILD` und `agent/kidctl_agent/__init__.py` erhöhen, dann 2.2 ausführen (baut Arch- und `.deb`-Pakete aus derselben Version). Die Kinderrechner bekommen das Update mit `sudo pacman -Syu` bzw. `sudo apt upgrade`. |
| Ab 0.27.0 | Das SDDM-Theme steckt jetzt im Paket `kidctl-plasma`. Auf bestehenden Plasma-Rechnern einmalig `sudo pacman -Syu kidctl-plasma`, sonst fehlt der Hinweis im Anmeldebildschirm. |

Ein Server-Update vor den Agenten ist unproblematisch, weil der Server auch die vorherige
Protokollversion unterstützt. Zu alte Agenten markiert das WebUI.

Ist im Repository eine neuere Agent-Version als auf einem Rechner, zeigen Übersicht, Geräteliste und
Geräteseite „Update: alt → neu“. Ist das Paket seit einem Tag veröffentlicht und der Rechner immer noch
nicht aktualisiert, kommt einmalig eine Push-Nachricht.

## Backup und Wiederherstellung

Der Server legt täglich ab 3 Uhr einen geprüften Schnappschuss der Datenbank an:
`data/backups/kidctl-JJJJ-MM-TT-HHMM.db.gz`. Die letzten 14 bleiben erhalten. Zeitpunkt und Größe des
letzten Backups stehen im WebUI unter **Einstellungen**, dort gibt es auch „Jetzt sichern“. Schlägt ein
Backup fehl oder gibt es seit 2 Tagen keines, kommt eine Push-Nachricht. Per Kommandozeile:
`docker compose exec kidctl python -m app.cli backup`.

Das externe Backup des Servers sollte `./data` mitsichern, dann sind die Schnappschüsse dabei.

Wiederherstellen:

```bash
docker compose stop kidctl
cp data/kidctl.db data/kidctl.db.vorher            # aktuellen Stand aufheben
gunzip -c data/backups/kidctl-2026-10-21-0300.db.gz > data/kidctl.db
rm -f data/kidctl.db-wal data/kidctl.db-shm
docker compose start kidctl
```

## Deinstallation (Kinderrechner)

```bash
# Arch
sudo pacman -R kidctl-plasma kidctl-gnome kidctl   # nur installierte nennen; nimmt PAM-, nsswitch-, nftables- und Mount-Änderungen zurück
sudo rm -rf /var/lib/kidctl    # Geräte-Token und Zustand (bleiben sonst erhalten)
# Debian/Ubuntu
sudo apt purge kidctl kidctl-gnome kidctl-plasma   # purge löscht auch /var/lib/kidctl
```

Danach im WebUI beim Gerät „Token widerrufen“. Den Repository-Eintrag bei Bedarf entfernen:
`[kidctl]` aus `/etc/pacman.conf` bzw. `/etc/apt/sources.list.d/kidctl.sources` und
`/etc/apt/keyrings/kidctl.asc`.

## Sprache

KidCTL spricht Deutsch und Englisch. Eltern wählen die Sprache im Profil (Standard: Browsersprache).
Auf dem Kinderrechner gilt die Sprache der Desktop-Sitzung des Kindes (Plasma: Systemeinstellungen →
Region & Sprache; GNOME: Einstellungen → System → Region & Sprache), vor der Anmeldung unter GNOME
die dort gewählte Sprache, sonst die Systemsprache aus `/etc/locale.conf`. Alles, was nicht mit `de`
beginnt, ergibt Englisch.
