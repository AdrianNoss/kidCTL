# Updates

| Was | Wie |
|---|---|
| Server | `git pull && docker compose up -d --build`. Datenbank-Migrationen (Alembic) laufen beim Start automatisch. |
| Umstieg SQLite → PostgreSQL (einmalig, Server bis 0.32.x) | Siehe [Umstieg auf PostgreSQL](postgresql-migration.de.md). |
| Client-Pakete | `pkgver` in `packaging/arch/PKGBUILD` und `agent/kidctl_agent/__init__.py` erhöhen, committen und auf main pushen, dann `kidctl-release.sh --apply` ([Paket-Repository](package-repo.de.md#bauen-und-veröffentlichen)) (Arch-, `.deb`- und Windows-Pakete aus derselben Version). Die Kinderrechner bekommen das Update mit `sudo pacman -Syu` bzw. `sudo apt upgrade`; Windows-Rechner installieren es selbst. |
| Ab 0.27.0 | Das SDDM-Theme steckt jetzt im Paket `kidctl-plasma`. Auf bestehenden Plasma-Rechnern einmalig `sudo pacman -Syu kidctl-plasma`, sonst fehlt der Hinweis im Anmeldebildschirm. |

Ein Server-Update vor den Agenten ist unproblematisch, weil der Server auch die vorherige
Protokollversion unterstützt. Zu alte Agenten markiert das WebUI.

Ist im Repository eine neuere Agent-Version als auf einem Rechner, zeigen Übersicht, Geräteliste und
Geräteseite „Update: alt → neu“. Ist das Paket seit einem Tag veröffentlicht und der Rechner immer noch
nicht aktualisiert, kommt einmalig eine Push-Nachricht.
