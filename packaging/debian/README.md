# Debian-/Ubuntu-Pakete (Client)

Gleicher Code wie das Arch-Paket, gebaut **ohne dpkg** auf dem Arch-Rechner (`build-deb.py`, nur
Python-Standardbibliothek). Unterstützt: Debian 13, Ubuntu 24.04/26.04 und darauf aufbauend (Kubuntu,
Linux Mint, Pop!_OS), amd64.

| Paket | Inhalt |
|---|---|
| `kidctl` (amd64) | Agent, Tray-App, `kidctl`-Befehl, systemd-Units, PAM-Profil (`pam-auth-update`), Browser-Policies (Firefox, Chromium, Chrome), Basisliste, mitgebrachte Bibliotheken für Python 3.12 |
| `kidctl-gnome` (all) | AppIndicator-Voreinstellung (Schema-Override), Eintrag „KidCTL“ |
| `kidctl-plasma` (all) | SDDM-Theme mit dem Hinweis der Login-Sperre |

## Bauen und testen

```bash
packaging/debian/build-deb.py /tmp/debs                     # .deb-Dateien
packaging/debian/apt-repo.py /tmp/debs /tmp/repo --key <FPR>  # signiertes APT-Repo (GNUPGHOME!)
```

Im Normalfall erledigt das `packaging/repo/kidctl-repo-build.sh` mit (Ergebnis unter `repo/deb`).
Test-VMs: `KIDCTL_VM_DISTRO=ubuntu|debian|ubuntu26 dev/vm/vm.sh …` (siehe `dev/vm/README.md`).

## Unterschiede zu Arch

| Thema | Debian/Ubuntu |
|---|---|
| Bibliotheken | Python 3.12 (Ubuntu 24.04 & Co.): `pydantic` 2 und `websockets` aus `vendor.txt` (Prüfsummen) unter `/usr/lib/kidctl/vendor/py3.12`; neuere Systeme nutzen ihre Pakete (`Depends: python3-pydantic (>= 2) \| python3 (<< 3.13)`) |
| Qt | PySide6, sonst PyQt6 (Ubuntu 24.04 hat kein PySide6) |
| Login-Sperre | `/usr/share/pam-configs/kidctl`, Priorität 200 (vor `sss` – dessen `sufficient pam_localuser` beendete die Prüfung sonst vorher) |
| Basisliste | `base-programs.txt` hier: Metapakete der Desktops (Depends + Recommends), Pseudogruppe `essential` |
| DNS-Filter | Unit startet `/usr/sbin/unbound` mit `AppArmorProfile=-unconfined` (Ubuntus unbound-Profil verbietet `/run/kidctl/dns`) |
| Entfernen | `apt remove` nimmt PAM, nsswitch, nftables, Mounts zurück; `apt purge` löscht auch `/var/lib/kidctl` |
