# Paket-Repository (Pacman und APT)

Die Pakete werden **auf dem eigenen Rechner** (Arch) gebaut und signiert – die Arch-Pakete und, ohne
`dpkg`, auch die `.deb`-Pakete samt APT-Repository unter `repo/deb`, der private Schlüssel kommt nie
auf den Server. Alle Skripte laufen ohne `--apply` nur als Probelauf. Interna der Skripte:
[`packaging/repo/README.md`](../packaging/repo/README.md).

## Signierschlüssel (einmalig)

```bash
packaging/repo/kidctl-repo-key.sh create            # Probelauf
packaging/repo/kidctl-repo-key.sh create --apply    # fragt nach einer Passphrase
```

- Eigener GPG-Ordner `~/.local/share/kidctl-repo/gnupg` (getrennt vom persönlichen Schlüsselbund),
  Ed25519, 3 Jahre gültig. Ausgabe: **Fingerabdruck** (für die Kinderrechner notieren) und `kidctl-repo.asc`.
- Passphrase vergeben. Am Ende zeigt das Skript den **Fingerabdruck**, den man für jeden
  Kinderrechner braucht.
- Name/Mail des Schlüssels über `KIDCTL_GPG_UID="Name <mail>"` änderbar.

> [!WARNING]
> **Privaten Schlüssel sichern** (Passwortmanager). Ohne ihn akzeptieren die Kinderrechner keine
> Updates mehr:
>
> ```bash
> GNUPGHOME=~/.local/share/kidctl-repo/gnupg gpg --armor --export-secret-keys > kidctl-repo-secret.asc
> ```

## Bauen und veröffentlichen

Die Pakete baut GitHub Actions, sobald ein Commit mit neuer Version auf main landet (der Workflow setzt das
Tag `v<Version>`); signiert und veröffentlicht wird lokal mit einem Befehl (braucht die
[gh CLI](https://cli.github.com/), angemeldet):

1. Version in `agent/kidctl_agent/__init__.py` und `packaging/arch/PKGBUILD` (`pkgver`) erhöhen (ein Test prüft
   die Übereinstimmung), committen, auf main pushen. Der Workflow `.github/workflows/release.yml` legt das Tag
   `v<Version>` an und baut Arch-, Debian- und Windows-Pakete (ohne Versionsänderung baut er nichts).
2. Lokal veröffentlichen – die Schlüssel liegen nie in GitHub. Ziel einmalig festlegen (oder
   `KIDCTL_REPO_TARGET` setzen):

```bash
echo "user@server:/srv/kidctl/data/repo/" > ~/.local/share/kidctl-repo/publish-target   # einmalig
packaging/repo/kidctl-release.sh            # Probelauf: zeigt Lauf und alle Schritte
packaging/repo/kidctl-release.sh --apply
```

Das Skript wartet auf den Bau, holt Linux-Pakete und Windows-Installer, signiert sie (GPG, `latest.json`) und
überträgt das Repo.
Einzelschritte, falls nötig: `kidctl-repo-build.sh --apply --ci [LAUF]`, `packaging/windows/build.py fetch
--apply [--run LAUF]`, `build.py sign --apply`, `kidctl-repo-publish.sh --apply`.
Ohne GitHub: `kidctl-repo-build.sh --apply` baut die Linux-Pakete lokal, `packaging/windows/build.py all
--apply` den Windows-Installer in der Windows-VM, danach `kidctl-repo-publish.sh --apply`.

Der SSH-Benutzer braucht Schreibrechte auf `data/repo`, und die Dateien müssen für den Container
lesbar sein (z. B. `chown <user> data/repo`, Rechte `755`/`644`). Der Server liefert `./data/repo` unter
`https://kids.example.org/repo/` aus. Test:
`https://kids.example.org/repo/kidctl.db` und
`https://kids.example.org/repo/deb/dists/stable/InRelease` lassen sich herunterladen.

Für die `.deb`-Pakete lädt der Bau beim ersten Mal die mitgebrachten Python-Bibliotheken für Ubuntu
24.04 (`pydantic`, `websockets`, Liste mit Prüfsummen in `packaging/debian/vendor.txt`) nach
`~/.cache/kidctl-vendor`.
