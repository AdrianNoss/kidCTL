# Arch-Pakete `kidctl`, `kidctl-plasma`, `kidctl-gnome` (Client)

Split-Paket aus einem PKGBUILD:

- `kidctl`: Agent, Tray-App, `kidctl`-Befehl, systemd-Units, PAM-Login-Sperre, noexec-, nftables- und
  Browser-Konfiguration.
- `kidctl-plasma`: SDDM-Theme mit dem Hinweis der Login-Sperre (hängt von `sddm`, `plasma-desktop` ab).
- `kidctl-gnome`: AppIndicator-Erweiterung als Voreinstellung für alle User (Schema-Override, damit GNOME
  das Tray-Symbol zeigt) und Eintrag „KidCTL“ in der Anwendungsübersicht (`kidctl-tray --show`).

Beide Desktop-Pakete legen eine Markierung unter `/usr/share/kidctl/desktop/` an; fehlt sie zu einem
installierten Desktop, meldet die Integritätsprüfung das.

## Bauen und installieren

```bash
cd packaging/arch
makepkg -f                       # im Repo-Checkout
sudo pacman -U kidctl-0*.pkg.tar.zst kidctl-plasma-*.pkg.tar.zst   # bzw. kidctl-gnome-*
sudo kidctl enroll https://kids.nossco.de <KOPPLUNGSCODE>
sudo systemctl enable --now kidctl-agent.service
```

In der Test-VM ist das Repo schreibgeschützt eingebunden:
`BUILDDIR=/var/tmp/kb PKGDEST=/var/tmp/kb makepkg -f` (nicht `/tmp` – wird beim Neustart geleert).

Danach den Rechner gegen physischen Zugriff härten: [`docs/haertung.md`](../../docs/haertung.md).

## Was das Paket einrichtet

| Pfad | Zweck |
|---|---|
| `/usr/lib/kidctl/` | Agent + `kidctl_common` (nicht in site-packages → kein Rebuild bei Python-Updates) |
| `/usr/bin/kidctl` | startet Python mit `-E -s` (keine PYTHON*-Variablen, keine User-Module – läuft als root) |
| `/usr/lib/systemd/system/kidctl-agent.service` | `Type=notify`, bereit erst nach geschriebener Sperrliste; `Before=systemd-user-sessions.service`; `Restart=always`; State in `/var/lib/kidctl`, Sperrliste in `/run/kidctl` (bleibt bei Neustart des Dienstes erhalten) |
| `/etc/pam.d/system-login` | kidctl-Block per Install-Skript (Sicherung `system-login.kidctl.bak`), beim Entfernen zurückgebaut |
| `/usr/lib/systemd/system/kidctl-noexec.service` | noexec für `/tmp`, `/var/tmp`, `/dev/shm` (vom Agenten per `Wants=` gestartet; `ExecStop` nimmt es zurück) |
| `/usr/lib/kidctl/base-programs.txt` | Basisliste der Programm-Whitelist (Wurzelpakete, Muster, Ausschlüsse) |
| `/usr/share/sddm/themes/kidctl/`, `/etc/sddm.conf.d/zz-kidctl-theme.conf` | (`kidctl-plasma`) Anmeldebildschirm mit Hinweis der Login-Sperre |
| `/usr/share/glib-2.0/schemas/90_kidctl.gschema.override` | (`kidctl-gnome`) AppIndicator-Erweiterung voreingestellt |
| `/usr/share/applications/kidctl.desktop` | (`kidctl-gnome`) „KidCTL“ in der GNOME-Anwendungsübersicht |

Der Dienst wird nicht automatisch aktiviert (Arch-Konvention). Beim Entfernen bleibt
`/var/lib/kidctl` (Geräte-Token) erhalten.

## namcap

Erwartete Meldungen: `$startdir`-Referenz (Code kommt aus dem Repo-Checkout; für das
Pacman-Repository später Quell-Tarball), `kidctl_common` „uninstalled“ (liegt im Paket),
Laufzeit-Abhängigkeiten „may not be needed“ (`notify-send`, `setpriv`, PAM, SDDM/GDM werden zur
Laufzeit aufgerufen).
