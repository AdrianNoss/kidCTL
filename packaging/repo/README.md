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

```bash
git tag v0.32.8 && git push origin v0.32.8           # startet den Bau (oder: gh workflow run release.yml)
gh run watch                                          # warten, bis der Lauf fertig ist
packaging/repo/kidctl-repo-build.sh --apply --ci      # Linux-Pakete holen, GPG-signieren, repo-add (alte Version raus)
packaging/windows/build.py fetch --apply              # Windows-Installer holen
packaging/windows/build.py sign --apply               # latest.json mit dem Update-Schlüssel signieren
KIDCTL_REPO_TARGET=user@server:/srv/kidctl/data/repo/ \
  packaging/repo/kidctl-repo-publish.sh           # Dry-Run (rsync -n)
KIDCTL_REPO_TARGET=user@server:/srv/kidctl/data/repo/ \
  packaging/repo/kidctl-repo-publish.sh --apply
```

`--ci` und `fetch` nehmen den Lauf zum Tag `v<Version>` (sonst den neuesten erfolgreichen; `--ci <Lauf-ID>`
bzw. `fetch --run <Lauf-ID>` wählen einen bestimmten). Lokal bauen geht weiterhin: `kidctl-repo-build.sh
--apply` ohne `--ci` (makepkg) und `build.py all --apply` (Windows-VM).

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
