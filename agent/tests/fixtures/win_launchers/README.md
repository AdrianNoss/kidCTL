# Launcher-Testdaten (Windows)

Echte Dateien aus der Test-VM (Snapshot „launchers“, 2026-11-19), anonymisiert (Steam-ID, Benutzername entfernt).
Grundlage für die Parser in `kidctl_agent/win/games.py` – neue Formate nur aus echten Installationen.

| Datei | Herkunft |
|---|---|
| `steam_libraryfolders.vdf`, `steam_appmanifest_*.acf` | `C:\Program Files (x86)\Steam\steamapps` (Bridge Constructor Portal, Steamworks Redistributables) |
| `epic_absolutedrift.item` | `C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests` |
| `battlenet_product.db` | `C:\ProgramData\Battle.net\Agent\product.db` (Hearthstone, Protobuf) |
| `ea_installerdata.xml` | `C:\Program Files\EA Games\SimCity 2000 SE\__Installer` |
| `gog_goggame.info` | Spielordner von Bio Menace (GOG) |
| `registry.json` | Auszug HKLM: `GOG.com\Games`, Uninstall-Einträge; `Ubisoft\Launcher\Installs` vom Rechner eines Nutzers (Starlink, 2026-11-22) |

Ubisoft Connect: Startmenü-Ziel und Ordner aus der VM, Spiel (Registry, Prozesskette `UbisoftGameLauncher.exe` ->
`games\<Spiel>\Starlink.exe`) vom Rechner eines Nutzers. Wargaming: Launcher installiert, aber kein Spiel – noch kein
Format.
