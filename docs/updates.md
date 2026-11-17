# Updates

| What | How |
|---|---|
| Server | `git pull && docker compose up -d --build`. Database migrations (Alembic) run automatically at startup. |
| Switch SQLite → PostgreSQL (one-time, server up to 0.32.x) | See [Switching to PostgreSQL](postgresql-migration.md). |
| Encryption per family (one-time) | **Before** `git pull && docker compose up -d --build`: create the master key (`docker compose run --rm kidctl python -m app.cli master-key`; with the old image it also works with `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"`), enter it as `KIDCTL_MASTER_KEY=…` in `.env` and **back it up separately**. At startup the migrations encrypt existing names, chats, log, statistics and email addresses. Without the key the server doesn't start. |
| Client packages | Increase `pkgver` in `packaging/arch/PKGBUILD` and `agent/kidctl_agent/__init__.py`, commit and push to main, then `kidctl-release.sh --apply` ([Package repository](package-repo.md#build-and-publish)) (Arch, `.deb` and Windows packages from the same version). The child computers get the update with `sudo pacman -Syu` or `sudo apt upgrade`; Windows computers install it themselves. |
| From 0.27.0 | The SDDM theme is now in the package `kidctl-plasma`. On existing Plasma computers run `sudo pacman -Syu kidctl-plasma` once, otherwise the notice on the sign-in screen is missing. |

Updating the server before the agents is fine because the server also supports the previous
protocol version. The web interface marks agents that are too old.

If the repository has a newer agent version than a computer, the overview, device list and
device page show "Update: old → new". If the package has been published for a day and the computer still
hasn't been updated, a push notification is sent once.
