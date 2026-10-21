# KidCTL

**English** · [Deutsch](README.de.md)

Self-hosted parental control for **Arch Linux with KDE Plasma or GNOME (Wayland)**. A central server with a
parents' web interface (installable as an app on your phone) controls a root agent on each child's
computer. All data stays on your own server – no cloud, no third-party account.

The web interface, emails, push notifications and everything the child sees (notifications, tray
app) are available in **English and German**. Parents choose their language in their profile
(default: browser language); on the child's computer the language of the desktop session is used.

## Features

- **Screen time**: daily limit and allowed hours per weekday, counted across all of a child's
  computers; warnings before the end, then sign-out and sign-in lock (SDDM, GDM and TTY). Works offline
  with the last known rules.
- **Game limit**: optional separate daily limit for games (Steam, Heroic and apps in the "Games"
  group); games are closed after a warning, the rest of the computer stays usable.
- **Requests and chat**: the child asks for more time, more game time or access to a blocked app or
  website from the tray app; parents answer from their phone (push notification) – "always allow",
  "today only" or "decline".
- **App allow list**: "monitor only" or "block" mode; catalogue of installed apps, Flatpak
  (system-wide), Steam and Heroic; programs in the home directory are blocked via noexec.
- **Web filter** (DNS): allow list with service bundles (YouTube, Wikipedia, learning apps,
  Microsoft 365) or block list (HaGeZi, OISD, StevenBlack); SafeSearch per child; browsers'
  DNS-over-HTTPS/DoT is blocked.
- **Reports**: statistics per day, app and game, an activity log of blocked actions and an optional
  weekly or monthly usage report by email. No history of allowed websites is stored.
- **Alerts**: device offline for too long, protection tampered with (PAM, nftables, noexec).
- **Security**: TOTP-only parent sign-in, rate limiting, CSRF protection, signed pacman repository
  for updates. Root and accounts in `wheel`/`sudo`/`admin` are **never** restricted.

## Installation

The step-by-step guide is currently available in German only:
**[docs/installation.md](docs/installation.md)** (server, pacman repository, child computers, web
interface) and **[docs/haertung.md](docs/haertung.md)** (hardening against physical access).

Short version:

```bash
# Server (Docker behind Traefik)
cp .env.example .env            # set SECRET_KEY, BASE_URL; Traefik network in docker-compose.yml
docker compose up -d --build
docker compose exec kidctl python -m app.cli create-parent

# Child's computer (after setting up the repository)
sudo pacman -S kidctl kidctl-plasma   # or kidctl-gnome (both if both desktops are installed)
sudo kidctl enroll https://kids.example.org <PAIRING-CODE>
sudo systemctl enable --now kidctl-agent.service
```

## Limitations

KDE Plasma and GNOME on Wayland only (no X11, no other desktops); no GDM auto-login for children; no protection against physical access without hardening; no idle
detection; no `flatpak --user`; Steam can only be controlled as a whole; VPNs are not detected. The
full list is in [`spec.md`](spec.md) (German).

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

## License

[PolyForm Noncommercial 1.0.0](LICENSE): free to use, modify and share for **non-commercial**
purposes (private use as well as schools, associations or public authorities). Commercial use is not
permitted. KidCTL is therefore "source-available", not "open source" in the OSI sense.

Copyright © 2026 Adrian Noss
