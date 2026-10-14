# Installation

Vollständige Einrichtung von KidCTL. Die Reihenfolge ist wichtig, weil jeder Teil auf dem vorherigen
aufbaut:

1. **Server**: Eltern-WebUI und Agent-API (Docker hinter Traefik)
2. **Pacman-Repository**: signierte Pakete für die Kinderrechner (auf dem eigenen Rechner gebaut)
3. **Kinderrechner**: Paket installieren, koppeln, Benutzer zuordnen
4. **Einstellungen** im WebUI
5. **Härtung** gegen physischen Zugriff

In den Beispielen steht `kids.example.org` für die eigene Subdomain.

## Voraussetzungen

| Komponente | Voraussetzung |
|---|---|
| Server | Linux mit Docker + Docker Compose, laufender **Traefik** mit certresolver `letsencrypt` und Middleware `secHeaders@file`, DNS-Eintrag für die Subdomain |
| Bau-Rechner | Arch Linux mit `base-devel`, `git`, `gnupg`, `rsync`, SSH-Zugang zum Server |
| Kinderrechner | Arch Linux mit **KDE Plasma (Wayland)** und **SDDM**, Internetzugang, ein Admin-Account mit `sudo` |
| Eltern | Smartphone mit Authenticator-App (TOTP, z. B. Aegis, Google Authenticator) |

## 1. Server

### 1.1 Code holen und Datenordner anlegen

```bash
git clone <repo-url> /srv/kidctl
cd /srv/kidctl
mkdir -p data
sudo chown 10001:10001 data     # der Container läuft als Benutzer 10001
```

`./data` enthält die SQLite-Datenbank, Sperrlisten und das Pacman-Repository. **Den Ordner extern
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

## 2. Pacman-Repository

Die Pakete werden **auf dem eigenen Rechner** gebaut und signiert, der private Schlüssel kommt nie
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
`https://kids.example.org/repo/kidctl.db` lässt sich herunterladen.

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
sudo pacman -S kidctl
```

Das Skript bricht ab, wenn der Schlüssel nicht zum Fingerabdruck passt. Bei der Installation trägt
das Paket die Login-Sperre in `/etc/pam.d/system-login` ein (Sicherung
`system-login.kidctl.bak`), stellt `/etc/nsswitch.conf` auf den DNS-Filter um und aktiviert das
SDDM-Theme sowie die Browser-Policies gegen DNS-over-HTTPS.

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
- **Zeit gutschreiben**: zusätzliche Minuten oder ein längeres Zeitfenster für ein bestimmtes Datum.
  Verlängerungsanfragen aus der Tray-App lassen sich auch direkt im Chat genehmigen.
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

Je Gerät: Alarm, wenn das Gerät länger als X Stunden offline ist (Standard 48 h).

## 5. Härtung

Ohne Härtung lässt sich jede Software-Sperre per USB-Stick oder Bootmenü umgehen. Mindestens
root sperren und Firmware- sowie Bootloader-Passwort setzen: [`docs/haertung.md`](haertung.md).

## Updates

| Was | Wie |
|---|---|
| Server | `git pull && docker compose up -d --build`. Datenbank-Migrationen laufen beim Start automatisch. |
| Client-Paket | `pkgver` in `packaging/arch/PKGBUILD` und `agent/kidctl_agent/__init__.py` erhöhen, dann 2.2 ausführen. Die Kinderrechner bekommen das Update mit `sudo pacman -Syu`. |

Ein Server-Update vor den Agenten ist unproblematisch, weil der Server auch die vorherige
Protokollversion unterstützt. Zu alte Agenten markiert das WebUI.

## Deinstallation (Kinderrechner)

```bash
sudo pacman -R kidctl          # nimmt PAM-, nsswitch-, nftables- und Mount-Änderungen zurück
sudo rm -rf /var/lib/kidctl    # Geräte-Token und Zustand (bleiben sonst erhalten)
```

Danach im WebUI beim Gerät „Token widerrufen“. Den Repository-Eintrag `[kidctl]` bei Bedarf
aus `/etc/pacman.conf` entfernen.
