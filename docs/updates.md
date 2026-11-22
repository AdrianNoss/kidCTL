# Updates

| What | How |
|---|---|
| Server | `git pull && docker compose up -d --build`. Database migrations (Alembic) run automatically at startup. |
| Switch SQLite → PostgreSQL (one-time, server up to 0.32.x) | See [Switching to PostgreSQL](postgresql-migration.md). |
| Encryption per family (one-time) | **Before** `git pull && docker compose up -d --build`: create the master key (`docker compose run --rm kidctl python -m app.cli master-key`; with the old image it also works with `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"`), enter it as `KIDCTL_MASTER_KEY=…` in `.env` and **back it up separately**. At startup the migrations encrypt existing names, chats, log, statistics and email addresses. Without the key the server doesn't start. |
| Client packages | Increase `pkgver` in `packaging/arch/PKGBUILD` and `agent/kidctl_agent/__init__.py`, add an entry with version, date and a short summary (DE/EN) at the top of `server/app/release_notes.py`, commit and push to main, then `kidctl-release.sh --apply` ([Package repository](package-repo.md#build-and-publish)) (Arch, `.deb` and Windows packages from the same version). The child computers get the update with `sudo pacman -Syu` or `sudo apt upgrade`; Windows computers install it themselves. |
| From 0.27.0 | The SDDM theme is now in the package `kidctl-plasma`. On existing Plasma computers run `sudo pacman -Syu kidctl-plasma` once, otherwise the notice on the sign-in screen is missing. |

Updating the server before the agents is fine because the server also supports the previous
protocol version. The web interface marks agents that are too old.

If the repository has a newer agent version than a computer, the overview, device list and
device page show "Update: old → new". If the package has been published for a day and the computer still
hasn't been updated, a push notification is sent once.

## Version and what's new

**Settings → About** (admin area: **About**) shows the running server version, the release date, the protocol version
and links to the project website, release notes, documentation, source code, license and security contact. In the
tray app on the child's computer, the **About** menu item shows the installed version.

After a server update, parents see a short summary of the changes on the overview once, with a link to the release
notes; **Got it** hides it until the next update.

If a newer version exists, the About page names it. For this, the server fetches `https://kidctl.de/version.json`
once a day, without identifiers or data about the family. Set `UPDATE_CHECK=0` in `.env` to turn this off.
