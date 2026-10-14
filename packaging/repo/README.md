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

```bash
packaging/repo/kidctl-repo-build.sh --apply      # makepkg --sign + repo-add --sign (alte Version raus)
KIDCTL_REPO_TARGET=user@server:/srv/kidctl/data/repo/ \
  packaging/repo/kidctl-repo-publish.sh           # Dry-Run (rsync -n)
KIDCTL_REPO_TARGET=user@server:/srv/kidctl/data/repo/ \
  packaging/repo/kidctl-repo-publish.sh --apply
```

Version vorher in `packaging/arch/PKGBUILD` (`pkgver`) und `agent/kidctl_agent/__init__.py`
erhöhen (ein Test prüft die Übereinstimmung). Der Server liefert `./data/repo` unter
`https://kids.nossco.de/repo/` aus.

## Kinderrechner: Repo einrichten (einmalig, als root)

```bash
curl -O https://kids.nossco.de/repo/kidctl-repo-client.sh   # oder aus dem Repo-Checkout
sudo bash kidctl-repo-client.sh https://kids.nossco.de/repo <FINGERABDRUCK>          # Dry-Run
sudo bash kidctl-repo-client.sh https://kids.nossco.de/repo <FINGERABDRUCK> --apply
sudo pacman -S kidctl        # Erstinstallation; danach Updates mit pacman -Syu
```

Das Skript prüft den heruntergeladenen Schlüssel gegen den Fingerabdruck, vertraut ihm lokal
(`pacman-key --lsign-key`) und trägt `[kidctl]` mit `SigLevel = Required DatabaseRequired` in
`/etc/pacman.conf` ein (Sicherung `pacman.conf.kidctl.bak`).

> Hinweis: `kidctl-repo-client.sh` liegt nicht automatisch im Repo-Verzeichnis – bei Bedarf mit
> nach `data/repo/` kopieren oder aus dem Git-Checkout verwenden.
