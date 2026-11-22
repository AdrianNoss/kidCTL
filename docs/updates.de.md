# Updates

| Was | Wie |
|---|---|
| Server | `git pull && docker compose up -d --build`. Datenbank-Migrationen (Alembic) laufen beim Start automatisch. |
| Umstieg SQLite → PostgreSQL (einmalig, Server bis 0.32.x) | Siehe [Umstieg auf PostgreSQL](postgresql-migration.de.md). |
| Verschlüsselung je Familie (einmalig) | **Vor** `git pull && docker compose up -d --build`: Hauptschlüssel erzeugen (`docker compose run --rm kidctl python -m app.cli master-key`, mit dem alten Image geht es auch per `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"`), als `KIDCTL_MASTER_KEY=…` in `.env` eintragen und **getrennt sichern**. Beim Start verschlüsseln die Migrationen vorhandene Namen, Chats, Protokoll, Statistik und E-Mail-Adressen. Ohne Schlüssel startet der Server nicht. |
| Client-Pakete | `pkgver` in `packaging/arch/PKGBUILD` und `agent/kidctl_agent/__init__.py` erhöhen, in `server/app/release_notes.py` oben einen Eintrag mit Version, Datum und Kurzfassung (DE/EN) ergänzen, committen und auf main pushen, dann `kidctl-release.sh --apply` ([Paket-Repository](package-repo.de.md#bauen-und-veröffentlichen)) (Arch-, `.deb`- und Windows-Pakete aus derselben Version). Die Kinderrechner bekommen das Update mit `sudo pacman -Syu` bzw. `sudo apt upgrade`; Windows-Rechner installieren es selbst. |
| Ab 0.27.0 | Das SDDM-Theme steckt jetzt im Paket `kidctl-plasma`. Auf bestehenden Plasma-Rechnern einmalig `sudo pacman -Syu kidctl-plasma`, sonst fehlt der Hinweis im Anmeldebildschirm. |

Ein Server-Update vor den Agenten ist unproblematisch, weil der Server auch die vorherige
Protokollversion unterstützt. Zu alte Agenten markiert das WebUI.

Ist im Repository eine neuere Agent-Version als auf einem Rechner, zeigen Übersicht, Geräteliste und
Geräteseite „Update: alt → neu“. Ist das Paket seit einem Tag veröffentlicht und der Rechner immer noch
nicht aktualisiert, kommt einmalig eine Push-Nachricht.

## Version und Neuigkeiten

Unter **Einstellungen → Info** (Verwaltungsbereich: **Info**) stehen die laufende Server-Version, das Release-Datum,
die Protokollversion und Links zu Projektseite, Release Notes, Doku, Quellcode, Lizenz und Sicherheitskontakt. In der
Tray-App auf dem Kinderrechner zeigt der Menüpunkt **Info** die installierte Version.

Nach einem Server-Update sehen Eltern auf der Übersicht einmalig eine Kurzfassung der Änderungen mit Link auf die
Release Notes; **Verstanden** blendet sie bis zum nächsten Update aus.

Gibt es eine neuere Version, nennt die Info-Seite sie. Dafür ruft der Server einmal täglich
`https://kidctl.de/version.json` ab, ohne Kennungen oder Daten über die Familie. Mit `UPDATE_CHECK=0` in `.env`
ist der Abruf abgeschaltet.
