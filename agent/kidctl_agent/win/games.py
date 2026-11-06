"""Spiele unter Windows erkennen: Steam (Bibliotheken, ``appmanifest_*.acf``) und Epic Games (Manifeste unter
``ProgramData``) über den Installationsordner – Umgebungsvariablen fremder Prozesse (``SteamAppId``) sind unter
Windows nicht ohne Weiteres lesbar. Store-Spiele erkennt der Katalog (``MicrosoftGame.config``).

Schlüssel wie unter Linux: ``game:steam:<appid>``, ``game:epic:<AppName>``. Namen und IDs stammen aus Dateien, die
Launcher anlegen – nur Anzeige, keine Freigaben.
"""

from __future__ import annotations

import json
import logging
import ntpath
import os
import re
from dataclasses import dataclass, field

from .catalog import norm

log = logging.getLogger("kidctl.games")

PROGRAM_DATA = os.environ.get("ProgramData", r"C:\ProgramData")
EPIC_MANIFESTS = ntpath.join(PROGRAM_DATA, r"Epic\EpicGamesLauncher\Data\Manifests")
MAX_FILE = 1024 * 1024
# Launcher: Ist er freigegeben, sind von ihm gestartete Prozesse erlaubt (wie Steam/Heroic unter Linux)
LAUNCHERS = {"steam.exe", "epicgameslauncher.exe", "battle.net.exe", "eadesktop.exe", "upc.exe",
             "galaxyclient.exe", "riotclientservices.exe"}
_VDF_PATH = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"', re.I)
_ACF = {k: re.compile(rf'"{k}"\s+"((?:[^"\\]|\\.)*)"', re.I) for k in ("appid", "name", "installdir")}


def _read(path: str) -> str:
    try:
        if os.path.getsize(path) > MAX_FILE:
            return ""
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _unescape(v: str) -> str:
    return v.replace("\\\\", "\\").replace('\\"', '"')


def steam_root() -> str | None:
    """Installationsordner von Steam (Registry), sonst ``None``."""
    try:
        import winreg
        for root, key, value in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(root, key) as k:
                    path = str(winreg.QueryValueEx(k, value)[0])
                    if os.path.isdir(path):
                        return path
            except OSError:
                continue
    except ImportError:
        pass
    return None


@dataclass
class GameIndex:
    """Installationsordner -> (Schlüssel, Name); dazu Bibliotheken und Ordner der Launcher."""
    games: list[tuple[str, str, str]] = field(default_factory=list)   # (normcase-Ordner, Schlüssel, Name)
    steam_dirs: list[str] = field(default_factory=list)              # Steam selbst + steamapps\common je Bibliothek
    epic_dirs: list[str] = field(default_factory=list)               # Installationsordner der Epic-Spiele
    steam_exe: str = ""

    def game_of(self, exe: str) -> tuple[str, str] | None:
        path = norm(exe)
        best = None
        for folder, key, name in self.games:
            if (path.startswith(folder + "\\")) and (best is None or len(folder) > len(best[0])):
                best = (folder, key, name)
        return (best[1], best[2]) if best else None

    def names(self) -> dict[str, str]:
        return {key: name for _f, key, name in self.games}


def scan(steam: str | None = None, epic_dir: str = EPIC_MANIFESTS, listdir=os.listdir, read=None) -> GameIndex:
    read = read or _read
    idx = GameIndex()
    steam = steam if steam is not None else steam_root()
    if steam:
        idx.steam_exe = ntpath.join(steam, "steam.exe")
        libraries = [steam]
        text = read(ntpath.join(steam, "steamapps", "libraryfolders.vdf"))
        for m in _VDF_PATH.finditer(text):
            lib = _unescape(m.group(1))
            if norm(lib) not in {norm(x) for x in libraries}:
                libraries.append(lib)
        idx.steam_dirs = [norm(steam)] + [norm(ntpath.join(lib, "steamapps", "common")) for lib in libraries]
        for lib in libraries:
            apps = ntpath.join(lib, "steamapps")
            try:
                names = listdir(apps)
            except OSError:
                continue
            for fn in names:
                if not re.fullmatch(r"appmanifest_\d+\.acf", fn):
                    continue
                text = read(ntpath.join(apps, fn))
                vals = {k: (m.group(1) if (m := r.search(text)) else "") for k, r in _ACF.items()}
                if vals["appid"].isdigit() and vals["installdir"] and "\\" not in vals["installdir"].strip("\\"):
                    folder = norm(ntpath.join(apps, "common", _unescape(vals["installdir"])))
                    idx.games.append((folder, f"game:steam:{vals['appid']}", _unescape(vals["name"])[:255] or
                                      vals["installdir"]))
    try:
        items = listdir(epic_dir)
    except OSError:
        items = []
    for fn in items:
        if not fn.lower().endswith(".item"):
            continue
        try:
            data = json.loads(read(ntpath.join(epic_dir, fn)) or "{}")
        except ValueError:
            continue
        app, loc = str(data.get("AppName", "")), str(data.get("InstallLocation", ""))
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", app) and loc and ntpath.isabs(loc):
            idx.games.append((norm(loc), f"game:epic:{app}", str(data.get("DisplayName", app))[:255]))
            idx.epic_dirs.append(norm(loc))
    return idx
