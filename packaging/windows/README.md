# Windows-Paket (Windows 10 22H2 / 11, x64)

Ein Installer (`KidCTL-Setup-<Version>.exe`, Inno Setup) mit eingebettetem Python 3.14 und allen Bibliotheken –
auf dem Kinderrechner muss nichts weiter installiert sein.

| Teil | Inhalt |
|---|---|
| `build.py` | Staging auf dem Linux-Rechner, Bau in der Windows-VM, Signatur der Updates |
| `vendor.lock` | Python (Embeddable) und Wheels mit SHA-256 – `build.py` lädt und prüft jede Datei |
| `kidctl.iss` | Inno-Setup-Skript: Dienst, Autostart der Tray-App, `kidctl://`, abgesicherter Modus, Rechte |
| `kidctl.ico` | Symbol (aus `packaging/arch/kidctl.svg`) |

## Bauen

Voraussetzung: Windows-VM mit Inno Setup (`dev/vm/winvm.sh`, richtet Inno Setup bei der Erstinstallation ein).

```bash
packaging/windows/build.py keygen --apply   # einmalig: Ed25519-Schlüssel für Updates
packaging/windows/build.py all --apply      # Staging, Installer in der VM bauen, latest.json signieren
packaging/repo/kidctl-repo-publish.sh --apply
```

- Schlüssel: `~/.local/share/kidctl-repo/windows-update.key` (0600) – **sicher aufbewahren**. Der öffentliche Teil
  steckt im Agenten (`kidctl_agent/win/update-key.pub`); ohne den passenden geheimen Schlüssel lassen sich
  installierte Rechner nicht mehr automatisch aktualisieren.
- Ergebnis: `~/.local/share/kidctl-repo/repo/windows/` mit Installer, `latest.json` und `latest.json.sig`; der
  Server liefert es unter `<BASE_URL>/repo/windows/`.
- Aufräumen: `sign` behält den neuesten Installer und eine ältere Version, ältere löscht es. Beim nächsten
  `kidctl-repo-publish.sh --apply` verschwinden sie auch vom Server.
- Zwischenspeicher: `~/.cache/kidctl-windows/` (Downloads, Staging). PySide6 wird auf QtCore/Gui/Widgets/
  Network/Svg verkleinert.

## Bau über GitHub Actions

[`.github/workflows/release.yml`](../../.github/workflows/release.yml) baut den Installer auf einem
GitHub-Runner (`build.py ci`) – ausgelöst durch einen Tag `v<Version>` oder von Hand – und lädt ihn als
Artefakt `KidCTL-Setup-unsigned` hoch. Ablauf je Release siehe [`packaging/repo/README.md`](../repo/README.md):

```bash
packaging/windows/build.py fetch --apply      # Installer des Laufs zum Tag v<Version> ins Repo holen
packaging/windows/build.py sign --apply       # latest.json mit dem Update-Schlüssel signieren
```

- Der geheime Update-Schlüssel bleibt auf dem Bau-Rechner; der öffentliche Teil liegt in
  `packaging/windows/update-key.pub` (`build.py` bricht ab, wenn beide nicht zusammenpassen).
- Authenticode-Signatur über SignPath ist vorbereitet, aber derzeit nicht aktiv (Antrag bei der SignPath
  Foundation im November 2026 abgelehnt, siehe [`docs/code-signing.md`](../../docs/code-signing.md)). Für
  einen späteren Antrag: Secret `SIGNPATH_API_TOKEN` und Variablen `SIGNPATH_ORGANIZATION_ID`,
  `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_SIGNING_POLICY_SLUG`, `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` in GitHub
  setzen, Vorlage [`signpath/artifact-configuration.xml`](signpath/artifact-configuration.xml); `fetch` nimmt
  dann automatisch den signierten Installer.
- Lokaler Bau in der Windows-VM (`build.py all --apply`) bleibt möglich, z. B. für Tests vor einem Release.

## Was der Installer einrichtet

- `C:\Program Files\KidCTL\` – `python\` (eingebettetes Python + Bibliotheken), `lib\` (kidctl), `kidctl.cmd`.
- `C:\ProgramData\KidCTL\` – Zustand, Logs, Updates; Rechte nur SYSTEM und Administratoren (Vererbung entfernt).
- Dienst `KidCTL` (LocalSystem, automatischer Start, Neustart bei Absturz), auch unter
  `HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal` und `\Network`.
- Tray-App im Autostart aller Benutzer (`HKLM\…\Run`), Startmenü-Eintrag „KidCTL“ mit AppUserModelID
  `KidCTL.Tray` (nötig für Toasts), Protokoll `kidctl://` für die Knöpfe in Toasts.
- Kopplung: Seite im Installer (Server-Adresse + Code) oder später als Administrator:
  `"C:\Program Files\KidCTL\kidctl.cmd" enroll https://kids.example.de ABCD-EFGH`.
- Stille Installation: `KidCTL-Setup-<Version>.exe /VERYSILENT /SERVER=https://… /CODE=ABCD-EFGH`.

## Updates

Der Agent prüft alle 6 Stunden `latest.json`, prüft die Ed25519-Signatur und die SHA-256 des Installers und
führt ihn still aus. Der Installer stoppt Dienst und Tray-Apps, ersetzt die Dateien und startet den Dienst; der
Agent startet die Tray-App in aktiven Kinder-Sitzungen neu.

## Deinstallation

„Apps → Installierte Apps → KidCTL“ (als Administrator). Vorher aktiviert der Uninstaller alle Konten, die
KidCTL gesperrt hat (`kidctl.cmd unlock-all`), danach entfernt er Dienst, Registry-Einträge und
`C:\ProgramData\KidCTL`.
