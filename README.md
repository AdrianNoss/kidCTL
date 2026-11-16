# KidCTL

**English** · [Deutsch](README.de.md)

Self-hosted parental control for **Windows 10 (22H2) and Windows 11 (23H2 or later)** (x64, Home and Pro) and
**Arch Linux, Debian and Ubuntu** (also Mint, Pop!_OS) **with KDE Plasma or GNOME (Wayland)**. A central server with a
parents' web interface (installable as an app on your phone) controls a root agent on each child's
computer. All data stays on your own server – no cloud, no third-party account.

The web interface, emails, push notifications and everything the child sees (notifications, tray
app) are available in **English and German**. Parents choose their language in their profile
(default: browser language); on the child's computer the language of the desktop session is used.

## Features

- **Screen time**: daily limit and allowed hours per weekday, counted across all of a child's
  computers; warnings before the end, then sign-out and sign-in lock (SDDM, GDM, TTY; Windows: the
  child's account is disabled). Works offline
  with the last known rules.
- **Game limit**: optional separate daily limit for games (Steam, Heroic and apps in the "Games"
  group); games are closed after a warning, the rest of the computer stays usable.
- **Requests and chat**: the child asks for more time, more game time or access to a blocked app or
  website from the tray app; parents answer from their phone (push notification) – "always allow",
  "today only" or "decline".
- **App allow list**: "monitor only" or "block" mode; catalogue of installed apps, Flatpak
  (system-wide), Steam and Heroic, on Windows Start menu and Store apps; programs in the home directory
  are blocked via noexec (Windows: execution ban on the user profile).
- **Web filter** (DNS): allow list with service bundles (YouTube, Wikipedia, learning apps,
  Microsoft 365) or block list (HaGeZi, OISD, StevenBlack); SafeSearch per child; browsers'
  DNS-over-HTTPS/DoT is blocked.
- **Reports**: statistics per day, app and game, an activity log of blocked actions and an optional
  weekly or monthly usage report by email. No history of allowed websites is stored.
- **Alerts**: device offline for too long, protection tampered with (PAM, nftables, noexec).
- **Security**: TOTP-only parent sign-in, rate limiting, CSRF protection, signed package repository (pacman and APT)
  for updates; Windows agents update themselves (Ed25519-signed). Root, accounts in `wheel`/`sudo`/`admin` and
  Windows administrators are **never** restricted.

## Installation

The step-by-step guide is currently available in German only:
**[docs/installation.md](docs/installation.md)** (server, package repository, child computers, web
interface) and **[docs/haertung.md](docs/haertung.md)** (hardening against physical access).

Short version:

```bash
# Server (Docker behind Traefik)
cp .env.example .env            # set SECRET_KEY, BASE_URL; Traefik network in docker-compose.yml
docker compose up -d --build
docker compose exec kidctl python -m app.cli create-parent

# Child's computer (after setting up the repository)
sudo pacman -S kidctl kidctl-plasma   # or kidctl-gnome (both if both desktops are installed)
sudo apt install kidctl kidctl-gnome  # Debian/Ubuntu (or kidctl-plasma)
sudo kidctl enroll https://kids.example.org <PAIRING-CODE>
sudo systemctl enable --now kidctl-agent.service
```

Windows child computer (as administrator): download `KidCTL-Setup-<version>.exe` from
`https://kids.example.org/repo/windows/` or from the [GitHub releases](https://github.com/AdrianNoss/kidCTL/releases),
run it and enter the server address and the pairing code. The installer is not code-signed yet, so SmartScreen asks
once ("More info" → "Run anyway").

## Limitations

Linux: KDE Plasma and GNOME on Wayland only (no X11, no other desktops), amd64 only on Debian/Ubuntu; no GDM
auto-login for children. Everywhere: no protection against physical access without hardening; no idle
detection; no `flatpak --user`; Steam can only be controlled as a whole; VPNs are not detected.

Windows: x64 only (no ARM). The sign-in screen shows no KidCTL notice – Windows hides the disabled account. The web
filter applies to the whole computer and follows the child signed in at the front (also for services and other users
in the background); Windows infrastructure (Update, Defender, SmartScreen …) is always allowed. Warning windows don't
appear over games in exclusive full screen (sign-out still happens). Children can't install programs themselves while
app control is active. Installer not code-signed: SmartScreen warning, possible false positives in Defender.

The full list is in [`spec.md`](spec.md) (German).

## Development

The specification ([`spec.md`](spec.md)), conventions ([`CLAUDE.md`](CLAUDE.md)) and code comments
are in German. Translations live in `server/app/locale/en.po`, `agent/kidctl_agent/locale/en.po` and
`tray/kidctl_tray/locale/en.po`; German is the source language in the code, and tests check that
every marked text has an English translation.

```bash
dev/devserver.sh                 # local server on 127.0.0.1:8000 (creates a test account)
cd server && python -m pytest -q tests
cd agent  && python -m pytest -q tests
cd tray   && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```

## Code signing policy

The Windows installers are currently **not code-signed**, so Windows SmartScreen asks for
confirmation on first launch. They are built from this repository by GitHub Actions; each release lists
the SHA-256 of the installer and links the build run. How releases are built and who is responsible:
[Code signing policy](docs/code-signing.md).

## Privacy

KidCTL only talks to the KidCTL server you set up yourself – no telemetry, no connection to the
developers or other third parties. See the [privacy statement](docs/code-signing.md#privacy).

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later). If you run a modified
server for others, you must offer them its source code; set `SOURCE_URL` to your fork so the
"Source code" link in the web interface points there.

Copyright © 2026 Adrian Noss
