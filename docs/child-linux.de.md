# Kinderrechner (Linux)

Alles als Admin auf dem Kinderrechner, sofern nicht anders angegeben.

## Kinder-Accounts

Jedes Kind bekommt einen **normalen** Account, also nicht in `wheel`:

```bash
sudo useradd -m -c "Max" max
sudo passwd max
```

> [!IMPORTANT]
> Accounts in `wheel`, `sudo` oder `admin` und root schränkt KidCTL grundsätzlich nie ein.
> So kann man sich als Admin nicht selbst aussperren.

## Repository einrichten und Paket installieren

```bash
curl -O https://kids.example.org/repo/kidctl-repo-client.sh
sudo bash kidctl-repo-client.sh https://kids.example.org/repo <FINGERPRINT>           # Probelauf
sudo bash kidctl-repo-client.sh https://kids.example.org/repo <FINGERPRINT> --apply
# Arch
sudo pacman -S kidctl kidctl-plasma   # KDE Plasma
sudo pacman -S kidctl kidctl-gnome    # GNOME
# Debian, Ubuntu, Mint, Pop!_OS
sudo apt install kidctl kidctl-gnome  # GNOME (Ubuntu, Debian, Pop!_OS 22.04)
sudo apt install kidctl kidctl-plasma # KDE Plasma (Kubuntu)
```

Den Fingerabdruck zeigt `kidctl-repo-key.sh` beim Anlegen des Schlüssels (siehe
[Paket-Repository](package-repo.de.md#signierschlüssel-einmalig)).

Das Skript erkennt die Distribution: Unter Arch prüft es den heruntergeladenen Schlüssel gegen den Fingerabdruck,
vertraut ihm lokal (`pacman-key --lsign-key`) und trägt `[kidctl]` mit `SigLevel = Required DatabaseRequired` in
`/etc/pacman.conf` ein (Sicherung `pacman.conf.kidctl.bak`), unter
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
  unter `/srv/steam/<child>` ab (`~/.steam/debian-installation` ist ein Verweis dorthin).

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

## Gerät koppeln

1. WebUI → „Geräte“ → „Gerät hinzufügen“ mit Namen, z. B. „Laptop Max“. Angezeigt wird ein
   Kopplungscode (8 Zeichen, 10 Minuten gültig).
2. Auf dem Kinderrechner:

   ```bash
   sudo kidctl enroll https://kids.example.org <PAIRING-CODE>
   sudo systemctl enable --now kidctl-agent.service
   ```

3. Im WebUI erscheint das Gerät als „online“.

## Kind anlegen und zuordnen

1. WebUI → „Kinder“ → Kind anlegen.
2. WebUI → Gerät → „Benutzer-Zuordnung“: die gemeldeten Linux-Accounts dem Kind zuordnen. Nicht zugeordnete Accounts bleiben
   uneingeschränkt.
3. Den Kinderrechner neu starten, damit alle Schutzmaßnahmen greifen (noexec-Home, Tray-App im
   Autostart, Anmeldebildschirm).

## Prüfen

```bash
sudo kidctl status                       # Policy und heutige Nutzung
systemctl status kidctl-agent
journalctl -u kidctl-agent -b            # Fehlersuche
```

Im WebUI zeigt der Abschnitt „Integrität“ beim Gerät, ob alle Schutzmaßnahmen aktiv sind. Fällt
später eine aus, kommt ein Push-Alarm.
