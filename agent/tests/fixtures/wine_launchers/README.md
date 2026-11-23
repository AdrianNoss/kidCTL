# Launcher-Testdaten (Linux, Wine)

Echte Daten aus der Test-VM (Arch, 2026-11-23), Benutzer `kind`; Testspiele sind Kopien von Wines `notepad.exe`.
Grundlage für `kidctl_agent/games.py` – neue Formate nur aus echten Installationen.

| Datei | Herkunft |
|---|---|
| `lutris_environ.txt` | `/proc/<pid>/environ` des Spielprozesses (`wine-preloader`, argv[0] = Unix-Pfad der EXE), Lutris 0.5.22 (Arch-Paket), Wine-Runner „system“, Spiel „Test-Spiel: Wüste“ |
| `bottles_environ.txt` | `/proc/<pid>/environ` des Programmprozesses (argv[0] = `C:\Spiele\Wüsten Rennen\Rennen.exe`), Bottles 67.7 (Flatpak), Runner soda-11.0-10, gestartet per `bottles-cli run -b Testflasche -p "Wüsten-Rennen"` |
| `bottles_bottle.yml` | `~/.var/app/com.usebottles.bottles/data/bottles/bottles/Testflasche/bottle.yml` (zwei Programme, eins mit Umlaut und umbrochenem Pfad) |

Beobachtet: Lutris setzt `GAME_NAME` (Anzeigename) und `LUTRIS_GAME_UUID` (je Start neu). Bottles setzt
`BOTTLE` (Name bzw. `Path` der Bottle) und `WINEPREFIX` (Bottle-Ordner); einen Programmnamen setzt Bottles nicht.
Wine-Hilfsprozesse (`services.exe`, `explorer.exe`, `wineserver` …) tragen dieselbe Umgebung.
