# Installation

Vollständige Einrichtung von KidCTL. Die Reihenfolge ist wichtig, weil jeder Teil auf dem vorherigen
aufbaut:

1. **[Server](server.de.md)**: Eltern-WebUI und Agent-API (Docker hinter Traefik)
2. **[Paket-Repository](package-repo.de.md)**: signierte Pakete für die Kinderrechner, Pacman und APT (auf dem
   eigenen Rechner gebaut)
3. **Kinderrechner**: Paket installieren, koppeln, Benutzer zuordnen – [Linux](child-linux.de.md) bzw.
   [Windows](child-windows.de.md)
4. **[Einstellungen](webui.de.md)** im WebUI
5. **[Härtung](hardening.de.md)** gegen physischen Zugriff

In den Beispielen steht `kids.example.org` für die eigene Subdomain.

## Voraussetzungen

| Komponente | Voraussetzung |
|---|---|
| Server | Linux mit Docker + Docker Compose, laufender **Traefik** mit certresolver `letsencrypt` und Middleware `secHeaders@file`, DNS-Eintrag für die Subdomain |
| Bau-Rechner | Arch Linux mit `base-devel`, `git`, `gnupg`, `rsync`, SSH-Zugang zum Server |
| Kinderrechner | **Arch Linux** mit KDE Plasma (SDDM) und/oder GNOME (GDM), jeweils unter Wayland, oder **Debian 13, Ubuntu 24.04/26.04 und darauf aufbauend** (Kubuntu, Linux Mint, Pop!_OS) mit GNOME oder Plasma auf 64-Bit-PCs (amd64); Internetzugang, ein Admin-Account mit `sudo`. Oder **Windows 10 22H2 / Windows 11 ab 23H2** (Home oder Pro, x64) mit einem Administratorkonto – siehe [Kinderrechner (Windows)](child-windows.de.md) |
| Eltern | Smartphone mit Authenticator-App (TOTP, z. B. Aegis, Google Authenticator) |

## Härtung

Ohne Härtung lässt sich jede Software-Sperre per USB-Stick oder Bootmenü umgehen. Mindestens
root sperren und Firmware- sowie Bootloader-Passwort setzen: [Härtung](hardening.de.md).

## Betrieb

- [Updates](updates.de.md)
- [Backup und Wiederherstellung](backup.de.md)
- [Umstieg auf PostgreSQL (einmalig)](postgresql-migration.de.md)
- [Deinstallation (Kinderrechner)](uninstall.de.md)
- [Spiele (Steam, Heroic)](games.de.md)
