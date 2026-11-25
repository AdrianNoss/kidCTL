# Limitations and known gaps

KidCTL makes it hard for children to get around the rules – but there is no such thing as complete security. This
page openly lists what KidCTL deliberately doesn't do and where known gaps are. It is the complete list; the short
version is in the README.

> [!NOTE]
> What is listed here does not count as a security vulnerability under the [security policy](../SECURITY.md). Please report new
> ways in which a child without admin rights gets around the time limit, app control or web filter confidentially.

## Everywhere

- **Physical access:** without [hardening](hardening.md), every lock can be bypassed, e.g. with a
  USB stick or via the boot menu. KidCTL itself only protects the running system.
- **No idle detection:** screen time keeps running as long as the child is signed in – even if they're
  not doing anything.
- **"Doesn't count" by open apps:** KidCTL sees which apps are running, not which window is in front. Apps outside
  the app list (e.g. downloaded portable apps) are not recognised as "another app" – except browsers. Without app
  control set to "Block", a child can use such an app next to a learning app without time running. KidCTL does not
  check what an exempted app itself shows (e.g. embedded web content).
- **VPN and fixed IP addresses:** the web filter works via DNS (name resolution). Anyone who opens a website directly
  by its IP address (including DNS-over-HTTPS via an IP address) or uses a VPN with fixed
  IP addresses bypasses it. VPNs are not actively detected.
- **Steam:** allowing Steam effectively allows everything that can be started via Steam – including via
  "Add a Non-Steam Game". The Steam library belongs to the child (Steam updates itself); Steam can only be
  controlled as a whole. Steam and Heroic offer no control of individual games.
- **Block list exceptions:** an exception doesn't apply to more specific entries of a block list (example: exception
  `example.com`, the list blocks `ads.example.com` – that stays blocked).
- **Chat:** no pictures or files, no read receipts.
- **Family deleted, device removed:** An agent that is not connected at that moment doesn't learn about it and
  keeps applying the last saved rules until it is uninstalled. Connected agents lift all restrictions when the family
  is deleted.
- **School holidays and public holidays offline:** The computer knows the rules for the next 7 days (school
  holidays and public holidays included). If it has no connection to the server for more than 6 days, that week
  repeats – the start or end of school holidays and public holidays after that only take effect once it is
  connected again.
- **No browsing history:** KidCTL deliberately doesn't store which allowed websites were visited – only what
  was blocked.
- **Server in a shared Docker network:** login locks and `ADMIN_ALLOW_NETS` use the address reported by Traefik,
  provided the connection comes from `TRUSTED_PROXIES` (default: private networks). Other containers in the same
  network can then pose as any address; the only remedy is to set `TRUSTED_PROXIES` to the address of the Traefik
  container (see [Server](server.md)).

## Linux

- **Desktops:** only KDE Plasma and GNOME on Wayland; no X11, no other desktops (Cinnamon and COSMIC
  are to follow).
- **Debian/Ubuntu:** 64-bit PCs (amd64) only.
- **GNOME:** no automatic login (GDM autologin) for children – with the sign-in lock active the
  screen would stay black. The web interface reports such a setting under "Integrity".
- **Ubuntu:** "Files" (Nautilus) is always allowed because Ubuntu's desktop icons need it.
- **Snap and Flatpak:** allowing a Snap or Flatpak app allows everything inside its sandbox (including
  `snap run --shell` or `flatpak run --command`). Flatpak apps that the child installs just for themselves
  (`flatpak --user`) are not supported.
- **Updates:** on Linux the agent doesn't update itself but comes with the normal system updates
  (pacman or apt).
- **Widgets and extensions:** Plasma widgets and GNOME extensions that the child installs themselves run
  inside the desktop shell and are not program starts. They don't get around the time limit, web filter or
  sign-out.
- **Shell commands:** commands such as `sh -c …` (e.g. via KRunner) are allowed because parts of the system need them.
  They can only start allowed programs, though: the child's own programs and scripts from their
  home folder don't run.
- **System scripts:** shell scripts from `/usr`, `/etc` and `/opt` are always allowed (the program they start is
  checked). Applications that consist only of a shell script under `/usr` therefore can't be
  blocked.
- **Helper programs:** programs that an allowed app calls internally (e.g. `git` from Kate) are blocked
  until they are allowed themselves.
- **Web filter:** programs that ask systemd's name resolution directly (e.g. `resolvectl`) bypass
  it; browsers and normal programs don't. If the firewall configuration is reloaded or removed, the
  filter can be open for up to 60 seconds until KidCTL restores it.

- **Tooltip on GNOME:** the AppIndicator extension shows no tooltip on hover. The remaining time is shown at the
  top of the icon's menu; warnings and the countdown work as before.
- **Lutris and Bottles:** KidCTL detects Windows games (Wine) started by Lutris and programs from a bottle's
  program list (see [Games](games.md#lutris-and-bottles)). Not detected are Linux games from Lutris and programs
  that run in Bottles only via "Run executable…" without an entry in the program list – they don't count towards
  game time or the game limit. As a workaround, use the "Counts as a game" switch in the program list, if the game
  appears there.
## Windows

- **x64 only:** no Windows on ARM.
- **Sign-in screen:** for the sign-in lock, KidCTL disables the account. Windows then hides it on the
  sign-in screen (via "Other user" only "account disabled" appears); there is no custom
  notice.
- **Web filter for the whole computer:** the filter of the child who is currently signed in at the front applies – also
  to services, background programs and users signed in in the background, e.g. a parent who
  stays signed in via "Switch user", and their company VPN.
- **Windows services are always allowed:** in allow-list mode, Windows Update, Defender, SmartScreen,
  time synchronisation, certificates, licences and the connectivity test stay reachable. Known background noise
  (telemetry, widgets, weather, Edge configuration) stays blocked and doesn't appear in the log. Bing is the
  default search in Edge and stays visible so that the child can ask for access.
- **Open connections:** after a user switch, blocking only applies to new connections; a running
  download of a child continues until it finishes. After a network adapter is added (e.g. VPN),
  Windows can use its DNS servers for up to 30 seconds.
- **Foreign DNS service:** at startup KidCTL stops any running `unbound.exe`, including one that doesn't belong to
  KidCTL.
- **Very short programs:** app control continuously checks started programs. Very short-lived programs
  (e.g. `reg.exe` from `cmd /c`) can slip through; the child's own programs still don't run because of the
  execution ban. `cmd /c …` is allowed because Windows components need it – what it starts is checked.
- **Allowed apps in the profile:** the child can put their own libraries (DLLs) into the folder of an allowed app
  in the user profile – as with Steam.
- **Installing on their own:** with app control active the child can't install anything (installer in the
  Downloads folder). Programs that Windows already blocks at startup show no notification and no "Ask for
  access" – the parents allow them in the web interface or briefly switch to "Off" for installing.
- **Other writable folders** (e.g. under `C:\ProgramData`) have no execution ban: programs in them that
  belong to the child are blocked, libraries (DLLs) from them are not.
- **Folders of released launchers:** if a launcher is released, programs in its game folders and (Battle.net) in
  `C:\ProgramData\Battle.net` run too – even if they belong to the child. These folders are writable for all users;
  a child could put its own programs there and start them.
- **Minecraft Launcher:** if it is released, programs named `java.exe`/`javaw.exe` in its package data in the child's
  profile (`AppData\Local\Packages\Microsoft.4297127D64EC6_…\LocalCache`) run – even if they belong to the child.
  The folder is writable for the child.
- **Store apps:** the built-in Microsoft apps show names from a fixed list; other Store apps the
  name from the app package.
- **Full-screen games:** warning windows don't appear over games in exclusive full screen; sign-out still
  happens.
- **Games without a launcher:** a game that is started directly via its `.exe`, without a launcher and without a
  known manifest, only counts as a game if "Counts as a game" is switched on for it in the program list.
- **Wargaming:** KidCTL only detects its games while the launcher starts them. The name
  comes from the `.exe`, and each `.exe` counts as a separate game.
- **Tray app:** a child could falsify the display of other users' tray apps. Enforcement by the
  KidCTL service is not affected.
- **No code signature:** SmartScreen asks on first installation, Defender may report false positives – see
  [Code signing](code-signing.md).
