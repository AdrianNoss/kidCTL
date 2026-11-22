# Uninstalling (child's computer)

```bash
# Arch
sudo pacman -R kidctl-plasma kidctl-gnome kidctl   # name only installed ones; reverts PAM, nsswitch, nftables and mount changes
sudo rm -rf /var/lib/kidctl    # device token and state (kept otherwise)
# Debian/Ubuntu
sudo apt purge kidctl kidctl-gnome kidctl-plasma   # purge also deletes /var/lib/kidctl
```

Then "Remove device" for the device in the web interface (the children's usage statistics are kept). Remove the repository entry if needed:
`[kidctl]` from `/etc/pacman.conf`, or `/etc/apt/sources.list.d/kidctl.sources` and
`/etc/apt/keyrings/kidctl.asc`.

Windows: see [Child's computer (Windows)](child-windows.md#uninstalling).
