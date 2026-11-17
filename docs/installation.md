# Installation

Complete setup of KidCTL. The order matters because each part builds on the previous
one:

1. **[Server](server.md)**: parents' web interface and agent API (Docker behind Traefik)
2. **[Package repository](package-repo.md)**: signed packages for the child computers, pacman and APT (built on
   your own computer)
3. **Child's computer**: install the package, pair, assign users – [Linux](child-linux.md) or
   [Windows](child-windows.md)
4. **[Settings](webui.md)** in the web interface
5. **[Hardening](hardening.md)** against physical access

In the examples, `kids.example.org` stands for your own subdomain.

## Requirements

| Component | Requirement |
|---|---|
| Server | Linux with Docker + Docker Compose, a running **Traefik** with certresolver `letsencrypt` and middleware `secHeaders@file`, DNS record for the subdomain |
| Build computer | Arch Linux with `base-devel`, `git`, `gnupg`, `rsync`, SSH access to the server |
| Child's computer | **Arch Linux** with KDE Plasma (SDDM) and/or GNOME (GDM), each on Wayland, or **Debian 13, Ubuntu 24.04/26.04 and derivatives** (Kubuntu, Linux Mint, Pop!_OS) with GNOME or Plasma on 64-bit PCs (amd64); internet access, an admin account with `sudo`. Or **Windows 10 22H2 / Windows 11 23H2 or later** (Home or Pro, x64) with an administrator account – see [Child's computer (Windows)](child-windows.md) |
| Parents | Smartphone with an authenticator app (TOTP, e.g. Aegis, Google Authenticator) |

## Hardening

Without hardening, every software lock can be bypassed with a USB stick or the boot menu. At least
lock root and set firmware and boot loader passwords: [Hardening](hardening.md).

## Operation

- [Updates](updates.md)
- [Backup and restore](backup.md)
- [Switching to PostgreSQL (one-time)](postgresql-migration.md)
- [Uninstalling (child's computer)](uninstall.md)
- [Games (Steam, Heroic)](games.md)
