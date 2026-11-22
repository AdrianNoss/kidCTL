# Deinstallation (Kinderrechner)

```bash
# Arch
sudo pacman -R kidctl-plasma kidctl-gnome kidctl   # nur installierte nennen; nimmt PAM-, nsswitch-, nftables- und Mount-Änderungen zurück
sudo rm -rf /var/lib/kidctl    # Geräte-Token und Zustand (bleiben sonst erhalten)
# Debian/Ubuntu
sudo apt purge kidctl kidctl-gnome kidctl-plasma   # purge löscht auch /var/lib/kidctl
```

Danach im WebUI beim Gerät „Gerät entfernen“ (die Nutzungsstatistik der Kinder bleibt erhalten). Den Repository-Eintrag bei Bedarf entfernen:
`[kidctl]` aus `/etc/pacman.conf` bzw. `/etc/apt/sources.list.d/kidctl.sources` und
`/etc/apt/keyrings/kidctl.asc`.

Windows: siehe [Kinderrechner (Windows)](child-windows.de.md#deinstallation).
