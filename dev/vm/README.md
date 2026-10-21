# Test-VM für den kidctl-Client

Arch Linux mit KDE Plasma (Wayland) unter QEMU/KVM – zum gefahrlosen Testen von Agent, PAM,
nftables und Mounts. **Systemnahe Client-Teile nie auf dem Entwicklungsrechner scharf schalten.**

## Einmalig auf dem Host

```bash
sudo pacman -S --needed qemu-desktop libisoburn openssl
```

`/dev/kvm` muss für den eigenen Benutzer beschreibbar sein (unter Arch Standard).

## VM anlegen und starten

```bash
dev/vm/vm.sh create            # Dry-Run: zeigt, was passiert
dev/vm/vm.sh create --apply    # lädt Arch-Cloud-Image (~550 MB), prüft SHA256, legt Disk + Seed an
dev/vm/vm.sh start             # Fenster mit 3D-Beschleunigung (virgl)
dev/vm/vm.sh wait              # wartet auf Paketinstallation (10–20 min beim ersten Start)
dev/vm/vm.sh ssh sudo reboot   # danach erscheint die grafische Anmeldung (SDDM)
dev/vm/vm.sh stop
dev/vm/vm.sh snapshot basis --apply   # sauberen Grundzustand sichern
```

Probleme mit dem Grafikfenster: `vm.sh start --no-gl` (ohne 3D, Plasma läuft dann mit
Software-Rendering). Ohne Fenster: `vm.sh start --headless` (nur SSH).

## Zugänge

| Benutzer | Passwort | Rolle |
|---|---|---|
| `admin` | `admin` | sudo ohne Passwort, SSH per eigenem Schlüssel (`vm.sh ssh`) |
| `kind` | `kind` | Standard-Account ohne Rechte – wird später einem Kind zugeordnet |

SSH-Passwort-Login ist aus, der Port ist nur lokal gebunden (`127.0.0.1:2222`).

## Verbindung zu Repo und Server

- Repo liegt schreibgeschützt unter `/opt/kidctl-src` (9p, live – Änderungen auf dem Host sind
  sofort sichtbar).
- Der Host ist aus der VM unter `10.0.2.2` erreichbar. Entwicklungsserver auf dem Host:
  `dev/devserver.sh` (Port 8000, Daten dauerhaft in `~/.local/share/kidctl-dev`, Eltern-Account
  `mama` wird beim ersten Start angelegt) → in der VM `http://10.0.2.2:8000`.
- `dev/webctl.py` bedient das WebUI per Kommandozeile (Kind anlegen, koppeln, zuordnen, Gutschrift,
  Programm-Modus …), z. B. `dev/webctl.py pair "VM"` → `sudo kidctl enroll --insecure-http http://10.0.2.2:8000 <code>`.

## GNOME in der VM

Die VM kann zusätzlich GNOME mit GDM haben (Rechner mit beiden Desktops; Snapshot `vor-gnome` ist der
Stand davor):

```bash
dev/vm/vm.sh ssh 'sudo pacman -Syu --needed gnome gdm gnome-shell-extension-appindicator'
dev/vm/vm.sh ssh 'sudo systemctl disable sddm && sudo systemctl enable gdm && sudo reboot'   # zurück: umgekehrt
```

Pakete aus dem Repo bauen und installieren: `kidctl`, `kidctl-plasma` und `kidctl-gnome` (siehe
`packaging/arch/README.md`). Keine automatische Anmeldung für `kind` unter GDM lassen – bei
Login-Sperre bleibt GDM sonst schwarz.

## Debian/Ubuntu-VMs

`KIDCTL_VM_DISTRO` wählt die VM (eigener Ordner, eigener SSH-Port):

| Wert | System | SSH | Zweck |
|---|---|---|---|
| `arch` (Standard) | Arch + Plasma/GNOME | 2222 | Arch-Paket |
| `ubuntu` | Ubuntu 24.04 + GNOME (Snapshot `basis`), zusätzlich Plasma/SDDM | 2223 | `.deb`, vollständige Tests |
| `debian` | Debian 13 ohne Oberfläche | 2224 | Kurztest Installation/Agent |
| `ubuntu26` | Ubuntu 26.04 ohne Oberfläche | 2225 | Kurztest Installation/Agent |

```bash
KIDCTL_VM_DISTRO=ubuntu dev/vm/vm.sh create --apply   # Provisionierung: provision-ubuntu.sh
KIDCTL_VM_DISTRO=ubuntu dev/vm/vm.sh start --headless
KIDCTL_VM_DISTRO=debian KIDCTL_VM_RAM=2G dev/vm/vm.sh start --headless   # provision-minimal.sh
```

Die Debian-Cloud-Images haben kein 9p – `.deb`-Dateien per `vm.sh ssh 'cat > /tmp/x.deb' < x.deb` kopieren.
Die serielle Konsole landet in `<VM-Ordner>/serial.log` (cloud-init-Meldungen).

## Snapshots (Rettung bei Aussperrung)

```bash
dev/vm/vm.sh stop
dev/vm/vm.sh snapshots                 # Liste
dev/vm/vm.sh restore basis --apply     # sichert vorher automatisch „vor-restore-<Zeit>“
```

Vor jedem Test von PAM-/nftables-Änderungen einen Snapshot anlegen. Der Admin-Account wird vom
Agenten nie eingeschränkt (nicht zugeordnet), SSH bleibt damit als Zugang erhalten.

## Dateien

| Pfad | Zweck |
|---|---|
| `dev/vm/vm.sh` | Verwaltung auf dem Host |
| `dev/vm/user-data.tmpl` | cloud-init: Benutzer, 9p-Mount, startet Provisionierung |
| `dev/vm/provision.sh` | läuft in der VM: Pakete, Locale/Tastatur (de), SDDM unter Wayland |
| `~/.local/share/kidctl-vm/` | Images, Disk, VM-eigener SSH-Schlüssel, Logs (`KIDCTL_VM_DIR`) |

Neu aufsetzen: VM stoppen, `~/.local/share/kidctl-vm/disk.qcow2` und `seed.iso` löschen,
`vm.sh create --apply` (Basis-Image wird wiederverwendet).
