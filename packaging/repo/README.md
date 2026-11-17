# Paket-Repository (Pacman und APT) – Interna

Bedienung (Signierschlüssel, Release veröffentlichen, Kinderrechner einrichten): Anleitung
[Paket-Repository](../../docs/package-repo.de.md) im Wiki.

Pakete werden **lokal signiert**; auf den Server kommt nur das fertige Repo. Der private Signierschlüssel verlässt
den Bau-Rechner nicht. Alle Skripte laufen ohne `--apply` als Dry-Run.

| Skript | Aufgabe |
|---|---|
| `common.sh` | gemeinsame Einstellungen (`KIDCTL_REPO_HOME`, GPG-Ordner, `--apply`/`run`) |
| `kidctl-repo-key.sh` | Signierschlüssel anlegen/exportieren (eigener GPG-Ordner, Ed25519) |
| `kidctl-repo-build.sh` | Pakete bauen bzw. mit `--ci` aus GitHub Actions holen, signieren, pacman- und APT-Repo aktualisieren |
| `kidctl-repo-publish.sh` | lokales Repo per rsync auf den Server (`KIDCTL_REPO_TARGET` oder `publish-target`) |
| `kidctl-release.sh` | alles in einem: Lauf abwarten, holen, signieren, veröffentlichen, GitHub-Release anlegen |
| `kidctl-repo-client.sh` | Kinderrechner: Repo und Schlüssel einrichten (wird mit dem Repo ausgeliefert) |

Lokal bauen geht weiterhin: `kidctl-repo-build.sh --apply` ohne `--ci` (makepkg) und
`packaging/windows/build.py all --apply` (Windows-VM).
