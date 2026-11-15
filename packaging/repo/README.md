# Pacman-Repository für kidctl

Pakete werden **lokal gebaut und signiert**; auf den Server kommt nur das fertige Repo. Der
private Signierschlüssel verlässt den Bau-Rechner nicht.

Alle Skripte laufen ohne `--apply` als Dry-Run.

## Einmalig: Signierschlüssel anlegen

```bash
packaging/repo/kidctl-repo-key.sh create            # Dry-Run
packaging/repo/kidctl-repo-key.sh create --apply    # fragt nach einer Passphrase
```

- Eigener GPG-Ordner `~/.local/share/kidctl-repo/gnupg` (getrennt vom persönlichen Schlüsselbund),
  Ed25519, 3 Jahre gültig. Name/Mail über `KIDCTL_GPG_UID` änderbar.
- Ausgabe: **Fingerabdruck** (für die Kinderrechner notieren) und `kidctl-repo.asc`.
- **Privaten Schlüssel sichern**, z. B. im Passwortmanager:
  `GNUPGHOME=~/.local/share/kidctl-repo/gnupg gpg --armor --export-secret-keys > kidctl-repo-secret.asc`
  Ohne ihn lassen sich keine Updates mehr signieren, die die Kinderrechner akzeptieren.

## Je Release: bauen und veröffentlichen

Gebaut wird in GitHub Actions (`.github/workflows/release.yml`: Arch-, Debian- und Windows-Pakete), signiert
und veröffentlicht lokal – die Schlüssel liegen nie in GitHub:

1. Version in `agent/kidctl_agent/__init__.py` und `packaging/arch/PKGBUILD` erhöhen, committen, auf main
   pushen. Der Workflow legt das Tag `v<Version>` an und baut (ohne Versionsänderung baut er nichts).
2. Lokal: **`packaging/repo/kidctl-release.sh --apply`** –
   wartet auf den Lauf, holt die Pakete, signiert sie (GPG, `latest.json`) und veröffentlicht.

Ziel der Veröffentlichung einmalig festlegen (oder `KIDCTL_REPO_TARGET` setzen):

```bash
echo "user@server:/srv/kidctl/data/repo/" > ~/.local/share/kidctl-repo/publish-target
packaging/repo/kidctl-release.sh            # Dry-Run: zeigt Lauf und alle Schritte
packaging/repo/kidctl-release.sh --apply
```

Einzelschritte, falls nötig: `kidctl-repo-build.sh --apply --ci [LAUF]`, `packaging/windows/build.py fetch
--apply [--run LAUF]`, `build.py sign --apply`, `kidctl-repo-publish.sh --apply`. Lokal bauen geht weiterhin:
`kidctl-repo-build.sh --apply` ohne `--ci` (makepkg) und `build.py all --apply` (Windows-VM).

Version vorher in `packaging/arch/PKGBUILD` (`pkgver`) und `agent/kidctl_agent/__init__.py`
erhöhen (ein Test prüft die Übereinstimmung). Der Server liefert `./data/repo` unter
`https://kids.nossco.de/repo/` aus.

## Kinderrechner: Repo einrichten (einmalig, als root)

```bash
curl -O https://kids.nossco.de/repo/kidctl-repo-client.sh   # liefert kidctl-repo-build.sh mit aus
sudo bash kidctl-repo-client.sh https://kids.nossco.de/repo <FINGERABDRUCK>          # Dry-Run
sudo bash kidctl-repo-client.sh https://kids.nossco.de/repo <FINGERABDRUCK> --apply
sudo pacman -S kidctl        # Erstinstallation; danach Updates mit pacman -Syu
```

Das Skript prüft den heruntergeladenen Schlüssel gegen den Fingerabdruck, vertraut ihm lokal
(`pacman-key --lsign-key`) und trägt `[kidctl]` mit `SigLevel = Required DatabaseRequired` in
`/etc/pacman.conf` ein (Sicherung `pacman.conf.kidctl.bak`).
