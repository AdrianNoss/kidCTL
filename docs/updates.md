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

## Update via button

From agent 0.46.0 the device page shows an **Update** button when an update is available. It starts the update on
the child's computer without you having to sign in there. The button only appears if the computer is connected and
its agent supports the update via button. Older agents have to be updated once as described above.

| System | What the agent runs |
|---|---|
| Arch | Refreshes only the kidCTL repository and updates `kidctl` and the installed `kidctl-plasma`/`kidctl-gnome` (no system update). The source comes from the `[kidctl]` entry in `/etc/pacman.conf`, signatures are mandatory. |
| Debian/Ubuntu | `apt-get update` only for `/etc/apt/sources.list.d/kidctl.sources` (with `Signed-By`), then `apt-get install --only-upgrade` for the kidCTL packages. |
| Windows | Runs the automatic update check right away (signed manifest, installer checksum). |

The server only sends the trigger. The agent itself decides on package, source and command. On Linux the package
manager runs as a separate systemd unit `kidctl-update`, because the update restarts the agent. You can find error
messages with `journalctl -u kidctl-update`.

The device page shows the status: **Update running**, **failed** (with message), **no newer package found** or,
after the agent has restarted, **Updated from old to new**. If no new version arrives within 15 minutes, it shows
"No response to the update" and you can try again.

> [!NOTE]
> The update runs right away, even if a child is signed in. Agent and tray app restart; the rules keep applying with
> the last known policy.
