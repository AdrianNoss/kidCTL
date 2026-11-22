# Updates

| Was | Wie |
|---|---|
| Server | `git pull && docker compose up -d --build`. Datenbank-Migrationen (Alembic) laufen beim Start automatisch. |
| Umstieg SQLite → PostgreSQL (einmalig, Server bis 0.32.x) | Siehe [Umstieg auf PostgreSQL](postgresql-migration.de.md). |
| Verschlüsselung je Familie (einmalig) | **Vor** `git pull && docker compose up -d --build`: Hauptschlüssel erzeugen (`docker compose run --rm kidctl python -m app.cli master-key`, mit dem alten Image geht es auch per `python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"`), als `KIDCTL_MASTER_KEY=…` in `.env` eintragen und **getrennt sichern**. Beim Start verschlüsseln die Migrationen vorhandene Namen, Chats, Protokoll, Statistik und E-Mail-Adressen. Ohne Schlüssel startet der Server nicht. |
| Client-Pakete | `pkgver` in `packaging/arch/PKGBUILD` und `agent/kidctl_agent/__init__.py` erhöhen, committen und auf main pushen, dann `kidctl-release.sh --apply` ([Paket-Repository](package-repo.de.md#bauen-und-veröffentlichen)) (Arch-, `.deb`- und Windows-Pakete aus derselben Version). Die Kinderrechner bekommen das Update mit `sudo pacman -Syu` bzw. `sudo apt upgrade`; Windows-Rechner installieren es selbst. |
| Ab 0.27.0 | Das SDDM-Theme steckt jetzt im Paket `kidctl-plasma`. Auf bestehenden Plasma-Rechnern einmalig `sudo pacman -Syu kidctl-plasma`, sonst fehlt der Hinweis im Anmeldebildschirm. |

Ein Server-Update vor den Agenten ist unproblematisch, weil der Server auch die vorherige
Protokollversion unterstützt. Zu alte Agenten markiert das WebUI.

Ist im Repository eine neuere Agent-Version als auf einem Rechner, zeigen Übersicht, Geräteliste und
Geräteseite „Update: alt → neu“. Ist das Paket seit einem Tag veröffentlicht und der Rechner immer noch
nicht aktualisiert, kommt einmalig eine Push-Nachricht.

## Update per Knopf

Ab Agent 0.46.0 zeigt die Geräteseite bei verfügbarem Update einen Knopf **Update**. Damit stößt du das Update auf
dem Kinderrechner an, ohne dich dort anzumelden. Der Knopf erscheint nur, wenn der Rechner verbunden ist und sein
Agent das Update per Knopf unterstützt. Ältere Agenten müssen einmal wie oben aktualisiert werden.

| System | Was der Agent ausführt |
|---|---|
| Arch | Frischt nur das kidCTL-Repository auf und aktualisiert `kidctl` sowie die installierten `kidctl-plasma`/`kidctl-gnome` (kein Systemupdate). Die Quelle stammt aus dem `[kidctl]`-Eintrag in `/etc/pacman.conf`, Signaturen sind Pflicht. |
| Debian/Ubuntu | `apt-get update` nur für `/etc/apt/sources.list.d/kidctl.sources` (mit `Signed-By`), danach `apt-get install --only-upgrade` für die kidCTL-Pakete. |
| Windows | Startet die automatische Update-Prüfung sofort (signiertes Manifest, Prüfsumme des Installers). |

Der Server sendet nur den Auslöser. Paket, Quelle und Befehl legt der Agent selbst fest. Unter Linux läuft der
Paketmanager als eigene systemd-Unit `kidctl-update`, weil das Update den Agenten neu startet. Fehlermeldungen findest
du mit `journalctl -u kidctl-update`.

Die Geräteseite zeigt den Stand: **Update läuft**, **fehlgeschlagen** (mit Meldung), **kein neueres Paket gefunden**
oder nach dem Neustart des Agenten **Aktualisiert von alt auf neu**. Kommt nach 15 Minuten keine neue Version, zeigt
sie „Update ohne Rückmeldung“, und du kannst es erneut versuchen.

> [!NOTE]
> Das Update läuft sofort, auch wenn ein Kind angemeldet ist. Agent und Tray-App starten neu; die Regeln gelten dabei
> mit der zuletzt bekannten Policy weiter.
