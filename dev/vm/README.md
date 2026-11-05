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

## Windows-VMs

`dev/vm/winvm.sh` (eigenes Skript, gleiche Konventionen: Dry-Run ohne `--apply`, Snapshots). UEFI mit
Secure-Boot-fähiger Firmware (OVMF), TPM 2.0 per `swtpm`, SATA-Disk und e1000e-Netz (keine virtio-Treiber nötig).

| `KIDCTL_WIN` | System | SSH | ISO |
|---|---|---|---|
| `win11` (Standard) | Windows 11 Home | 2226 | `~/.local/share/kidctl-vm-win11/install.iso` |
| `win10` | Windows 10 Pro | 2227 | `~/.local/share/kidctl-vm-win10/install.iso` |

Zusätzlich auf dem Host: `sudo pacman -S --needed edk2-ovmf swtpm`. Die ISOs selbst bei Microsoft laden
(deutsch, x64); Windows bleibt unaktiviert (reicht für Tests).

```bash
dev/vm/winvm.sh create --apply   # Disk, UEFI-Variablen, TPM, Antwortdatei-ISO
dev/vm/winvm.sh start            # erster Start: Installation (Taste für „von CD starten“ drückt das Skript)
dev/vm/winvm.sh wait             # bis SSH und Einrichtung fertig sind (20–40 min)
dev/vm/winvm.sh stop && dev/vm/winvm.sh snapshot basis --apply
dev/vm/winvm.sh ssh              # PowerShell als admin
dev/vm/winvm.sh put datei C:/ziel/ ; dev/vm/winvm.sh get C:/quelle ziel
```

- **Windows 11 ab 24H2:** Setup ignoriert den WinPE-Teil der Antwortdatei teilweise und fragt nach dem Product Key
  – einmal „Ich habe keinen Product Key“ wählen; Partitionierung, Edition, Konten und Einrichtung laufen danach
  unbeaufsichtigt.
- Konten: `admin`/`admin` (Administrator, SSH per Schlüssel der VM), `kind`/`kind` und `kind2`/`kind2`
  (Standardbenutzer, legt `provision-windows.ps1` an). Einrichtung: OpenSSH-Server (PowerShell als Shell),
  kein Ruhezustand, Inno Setup (für `packaging/windows/build.py`). Log: `C:\kidctl-provision.log`.
- Snapshots enthalten Disk, UEFI-Variablen und TPM-Zustand.
- Server auf dem Host wie bei Linux: `http://10.0.2.2:8000` (`dev/devserver.sh`).
- `dev/vm/winsync.sh`: Code aus dem Arbeitsbaum in die installierte VM kopieren und den Dienst neu starten
  (statt Installer bauen).
- `dev/vm/vmctl.py <qmp.sock> type|key|click|shot`: Tastatur (deutsches Layout), Mausklick, Screenshot über QMP –
  z. B. zum Anmelden als Kind: `vmctl.py ~/.local/share/kidctl-vm-win11/qmp.sock type kind`.
