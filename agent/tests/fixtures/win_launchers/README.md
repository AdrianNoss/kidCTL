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
| `registry.json` | Auszug HKLM: `GOG.com\Games`, Uninstall-Einträge |

Ubisoft Connect und Wargaming: Launcher installiert, aber kein Spiel (zu groß) – noch kein Format.
