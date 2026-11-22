r"""Spiele unter Windows erkennen – über den Installationsordner (Umgebungsvariablen fremder Prozesse wie
``SteamAppId`` sind unter Windows nicht ohne Weiteres lesbar). Store-Spiele erkennt der Katalog
(``MicrosoftGame.config``). Formate nur nach echten Installationen
(Testdaten: ``agent/tests/fixtures/win_launchers``):

- Steam: Bibliotheken (``libraryfolders.vdf``) und ``appmanifest_*.acf`` -> ``game:steam:<appid>``
- Epic Games: ``ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item`` (JSON) -> ``game:epic:<AppName>``
- GOG Galaxy: Registry ``HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\<id>`` (``path``, ``gameName``) -> ``game:gog:<id>``
- EA app: Uninstall-Eintrag, dessen ``InstallLocation`` ``__Installer\installerdata.xml`` enthält -> ``game:ea:<Schlüssel>``
- Battle.net: ``ProgramData\Battle.net\Agent\product.db`` (Protobuf: je Produkt UID, Code, Pfad) ->
  ``game:battlenet:<uid>``; Name = Ordnername (die Datei enthält keinen Anzeigenamen)
- Ubisoft Connect: Registry ``HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\<id>`` (``InstallDir``) ->
  ``game:ubisoft:<id>``; Name = Ordnername. Spiele liegen standardmäßig unter dem Launcher-Ordner (``…\games``).
- Wargaming: noch kein Format aus echter Installation – nur der Rückfall unten.

Namen und IDs stammen aus Dateien, die Launcher anlegen – nur Anzeige, keine Freigaben.

Rückfall (``LaunchTracker``): Ein Prozess, den ein Launcher direkt oder über Zwischenprozesse gestartet hat, zählt
als Spiel (``game:exe:<Pfad>``) – ohne die Hilfsprozesse der Launcher (Web-Helfer, Crash-Reporter, Updater …).
Die Abstammung wird beim ersten Sehen des Prozesses gemerkt und bleibt, auch wenn der Launcher sich beendet.

Der Index wird neu gelesen, sobald sich ein Manifest-Ordner ändert (``signature``), nicht erst mit dem Katalog.
"""

from __future__ import annotations

import functools
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
BNET_DB = ntpath.join(PROGRAM_DATA, r"Battle.net\Agent\product.db")
EA_INSTALL_DATA = ntpath.join(PROGRAM_DATA, r"EA Desktop\InstallData")
UBI_KEYS = (r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs",)
GOG_KEYS = (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games")
UNINSTALL_KEYS = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                  r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
STEAM_TOOLS = {"228980"}             # Steamworks Common Redistributables – kein Spiel
BNET_TOOLS = {"agent", "battle.net", "bna"}   # Battle.net selbst (UID bzw. Produktcode)
_ID = re.compile(r"[A-Za-z0-9_.{}-]{1,128}")
MAX_FILE = 1024 * 1024
# Launcher: Ist er freigegeben, sind von ihm gestartete Prozesse erlaubt (wie Steam/Heroic unter Linux)
LAUNCHERS = {"steam.exe", "epicgameslauncher.exe", "battle.net.exe", "eadesktop.exe", "upc.exe",
             "galaxyclient.exe", "riotclientservices.exe", "wgc.exe"}
# Startmenü-Ziele, die den Launcher nur starten (echte Installationen, VM-Snapshot „launchers“): Freigegeben wird
# der Eintrag, er steht für den Launcher daneben
LAUNCHER_ENTRIES = {"battle.net launcher.exe": "battle.net.exe", "ealauncher.exe": "eadesktop.exe",
                    "ubisoftconnect.exe": "upc.exe"}
# Minecraft Launcher (Store-App, echter Fall 2026-11-22): startet Minecraft: Java Edition mit einer Java-Laufzeit, die er
# in die Paketdaten des Kindes lädt (``…\AppData\Local\Packages\<PFN>\LocalCache\…\bin\javaw.exe``, ohne
# App-Identität). Diese Java-Prozesse gehören zum Paket und zählen als Spiel.
MINECRAFT_PFN = "microsoft.4297127d64ec6_8wekyb3d8bbwe"
MINECRAFT_JAVA = ("game:minecraft:java", "Minecraft: Java Edition")
_PKG_CACHE = re.compile(r"^[a-z]:\\users\\[^\\]+\\appdata\\local\\packages\\([^\\]+)\\localcache\\")
_JAVA = {"java.exe", "javaw.exe"}


def package_runtime(exe: str) -> str:
    """Package Family Name (klein), wenn ``exe`` (normiert) die Java-Laufzeit des Minecraft Launchers ist, sonst ``""``.
    Bewusst eng (nur dieses Paket, nur ``java[w].exe``): der Ordner ist für das Kind beschreibbar."""
    m = _PKG_CACHE.match(exe)
    return m.group(1) if m and m.group(1) == MINECRAFT_PFN and ntpath.basename(exe) in _JAVA else ""


# Hilfsprozesse außerhalb des Programmordners: Battle.net-Agent in ``Agent`` bzw. versionierten Ordnern ``Agent.NNNN``
LAUNCHER_DATA_DIRS = {"battle.net.exe": (norm(ntpath.join(PROGRAM_DATA, "Battle.net")),)}
# Hilfsprozesse der Launcher: zählen nicht als Spiel und vererben die Abstammung nicht
LAUNCHER_HELPERS = {
    "steamwebhelper.exe", "steamservice.exe", "steamerrorreporter.exe", "steamerrorreporter64.exe",
    "gameoverlayui.exe", "gameoverlayui64.exe", "steamsysinfo.exe", "steam_monitor.exe",
    "epicwebhelper.exe", "unrealcefsubprocess.exe", "epiconlineservicesuserhelper.exe",
    "epiconlineserviceshost.exe", "crashreportclient.exe",
    "agent.exe", "agenthelper.exe", "battle.net helper.exe", "blizzarderror.exe", "blizzard update agent.exe",
    "eabackgroundservice.exe", "eaconnect_microsoft.exe", "qtwebengineprocess.exe", "eacefsubprocess.exe",
    "eacrashreporter.exe", "link2ea.exe", "ealocalhostsvc.exe",
    "upcwebhelper.exe", "uplaywebcore.exe", "ubisoftconnectwebcore.exe", "uplayservice.exe", "upc_crashreporter.exe",
    "galaxyclient helper.exe", "galaxycommunication.exe", "gog galaxy notifications renderer.exe",
    "riotclientux.exe", "riotclientuxrender.exe", "riotclientcrashhandler.exe",
    "wgc_renderer_host.exe", "wgc_helper.exe",
    "conhost.exe", "werfault.exe", "werfaultsecure.exe", "dxsetup.exe", "vc_redist.x64.exe", "vc_redist.x86.exe",
}
_HELPER_WORDS = ("crash", "webhelper", "cefprocess", "cefsubprocess", "renderer", "updater", "update agent",
                 "overlay", "reporter", "unins", "redist", "setup")
STEAM_DEFAULT = ntpath.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam")
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


def steam_root(isdir=os.path.isdir) -> str | None:
    """Installationsordner von Steam: Registry (Maschine, sonst je Benutzer – der Agent läuft als SYSTEM und sieht
    ``HKCU`` der Kinder nur unter ``HKEY_USERS``), sonst der Standardordner; ``None``, wenn nichts davon existiert."""
    for path in _steam_registry_paths():
        path = path.replace("/", "\\")
        if path and isdir(path):
            return path
    return STEAM_DEFAULT if isdir(ntpath.join(STEAM_DEFAULT, "steamapps")) else None


def _steam_registry_paths() -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for root, key, value in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(root, key) as k:
                out.append(str(winreg.QueryValueEx(k, value)[0]))
        except OSError:
            continue
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, "") as users:
            i = 0
            while True:
                try:
                    sid = winreg.EnumKey(users, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(users, sid + r"\Software\Valve\Steam") as k:
                        out.append(str(winreg.QueryValueEx(k, "SteamPath")[0]))
                except OSError:
                    continue
    except OSError:
        pass
    return out


@dataclass
class GameIndex:
    """Installationsordner -> (Schlüssel, Name); dazu Bibliotheken und Ordner der Launcher."""
    games: list[tuple[str, str, str]] = field(default_factory=list)   # (normcase-Ordner, Schlüssel, Name)
    steam_dirs: list[str] = field(default_factory=list)              # Steam selbst + steamapps\common je Bibliothek
    epic_dirs: list[str] = field(default_factory=list)               # Installationsordner der Epic-Spiele
    steam_exe: str = ""
    watch: list[str] = field(default_factory=list)                   # Manifest-Ordner/-Dateien (Änderung -> neu lesen)
    watch_reg: list[str] = field(default_factory=list)               # Registry-Schlüssel (HKLM) ebenso
    launcher_dirs: dict[str, list[str]] = field(default_factory=dict)   # Launcher (Dateiname) -> Spieleordner

    def game_of(self, exe: str) -> tuple[str, str] | None:
        path = norm(long_path(exe))
        best = None
        for folder, key, name in self.games:
            if (path.startswith(folder + "\\")) and (best is None or len(folder) > len(best[0])):
                best = (folder, key, name)
        return (best[1], best[2]) if best else None

    def names(self) -> dict[str, str]:
        return {key: name for _f, key, name in self.games}


def scan(steam: str | None = None, epic_dir: str = EPIC_MANIFESTS, listdir=os.listdir, read=None, registry=None,
         read_bytes=None, isfile=os.path.isfile, bnet_db: str = BNET_DB) -> GameIndex:
    read = read or _read
    registry = registry or reg_subkeys
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
        idx.watch += [ntpath.join(steam, "steamapps", "libraryfolders.vdf")] + \
            [ntpath.join(lib, "steamapps") for lib in libraries]
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
                if vals["appid"].isdigit() and vals["appid"] not in STEAM_TOOLS and vals["installdir"] \
                        and "\\" not in vals["installdir"].strip("\\"):
                    folder = norm(ntpath.join(apps, "common", _unescape(vals["installdir"])))
                    idx.games.append((folder, f"game:steam:{vals['appid']}", _unescape(vals["name"])[:255] or
                                      vals["installdir"]))
    idx.watch.append(epic_dir)
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
    idx.launcher_dirs["epicgameslauncher.exe"] = list(idx.epic_dirs)
    _add(idx, "galaxyclient.exe", gog_games(registry, GOG_KEYS))
    idx.watch_reg += list(GOG_KEYS)
    _add(idx, "eadesktop.exe", ea_games(registry, UNINSTALL_KEYS, isfile))
    idx.watch_reg += list(UNINSTALL_KEYS)
    idx.watch.append(EA_INSTALL_DATA)
    _add(idx, "upc.exe", ubisoft_games(registry, UBI_KEYS))
    idx.watch_reg += list(UBI_KEYS)
    _add(idx, "battle.net.exe", battlenet_games((read_bytes or _read_bytes)(bnet_db)))
    idx.watch.append(bnet_db)
    return idx


def _add(idx: GameIndex, launcher: str, found: list[tuple[str, str, str]]) -> None:
    idx.games += found
    idx.launcher_dirs[launcher] = [folder for folder, _k, _n in found]


def _abs_dir(path: str) -> str:
    path = (path or "").strip().replace("/", "\\").rstrip("\\")
    return norm(path) if ntpath.isabs(path) and len(path) > 3 else ""   # nie ein ganzes Laufwerk


def gog_games(registry, keys=GOG_KEYS) -> list[tuple[str, str, str]]:
    """GOG Galaxy: je Spiel ein Registry-Schlüssel (Name = Spiel-ID) mit ``path`` und ``gameName``."""
    out = []
    for name, values in registry(keys):
        folder = _abs_dir(str(values.get("path", "")))
        if name.isdigit() and folder:
            out.append((folder, f"game:gog:{name}", str(values.get("gameName") or name)[:255]))
    return out


def ubisoft_games(registry, keys=UBI_KEYS) -> list[tuple[str, str, str]]:
    """Ubisoft Connect: je Spiel ein Registry-Schlüssel (Name = Spiel-ID) mit ``InstallDir`` (mit ``/``)."""
    out = []
    for name, values in registry(keys):
        raw = str(values.get("InstallDir", ""))
        folder = _abs_dir(raw)
        if name.isdigit() and folder:
            label = ntpath.basename(raw.replace("/", "\\").rstrip("\\"))
            out.append((folder, f"game:ubisoft:{name}", label[:255]))
    return out


def ea_games(registry, keys=UNINSTALL_KEYS, isfile=os.path.isfile) -> list[tuple[str, str, str]]:
    """EA app: Spiele haben einen Uninstall-Eintrag, im Installationsordner liegt ``__Installer\\installerdata.xml``."""
    out = []
    for name, values in registry(keys):
        folder = _abs_dir(str(values.get("InstallLocation", "")))
        if folder and _ID.fullmatch(name) and isfile(ntpath.join(folder, "__Installer", "installerdata.xml")):
            out.append((folder, f"game:ea:{name.lower()}", str(values.get("DisplayName") or name)[:255]))
    return out


def battlenet_games(data: bytes) -> list[tuple[str, str, str]]:
    """Battle.net ``product.db``: Feld 1 (wiederholt) = Installation {1: UID, 2: Produktcode, 3: {1: Pfad}}."""
    out = []
    try:
        for field_no, value in _pb_fields(data):
            if field_no != 1 or not isinstance(value, bytes):
                continue
            inst = {f: v for f, v in _pb_fields(value) if isinstance(v, bytes) and f in (1, 2, 3)}
            uid = inst.get(1, b"").decode("utf-8", "replace")
            code = inst.get(2, b"").decode("utf-8", "replace")
            path = next((v.decode("utf-8", "replace") for f, v in _pb_fields(inst.get(3, b""))
                         if f == 1 and isinstance(v, bytes)), "")
            folder = _abs_dir(path)
            if folder and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", uid) and uid.lower() not in BNET_TOOLS \
                    and code.lower() not in BNET_TOOLS:
                out.append((folder, f"game:battlenet:{uid}", ntpath.basename(path.replace("/", "\\").rstrip("\\"))[:255]))
    except ValueError:
        log.warning("Battle.net product.db nicht lesbar")
    return out


def _pb_fields(data: bytes):
    """Minimaler Protobuf-Leser: (Feldnummer, Wert) – Varint als int, längenbegrenzt als bytes."""
    def varint(i: int) -> tuple[int, int]:
        result = shift = 0
        while True:
            if i >= len(data) or shift > 63:
                raise ValueError("Varint")
            b = data[i]
            i += 1
            result |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                return result, i
    i = 0
    while i < len(data):
        key, i = varint(i)
        field_no, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(i)
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            n, i = varint(i)
            if i + n > len(data):
                raise ValueError("Länge")
            value, i = data[i:i + n], i + n
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError(f"Wire-Typ {wire}")
        yield field_no, value


def _read_bytes(path: str) -> bytes:
    try:
        if os.path.getsize(path) > MAX_FILE:
            return b""
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def reg_subkeys(keys) -> list[tuple[str, dict]]:
    """(Name, Werte) aller Unterschlüssel der ``HKLM``-Schlüssel ``keys``; ohne ``winreg`` leer."""
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for key in keys:
        try:
            parent = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key)
        except OSError:
            continue
        with parent:
            i = 0
            while i < 5000:
                try:
                    name = winreg.EnumKey(parent, i)
                except OSError:
                    break
                i += 1
                values: dict = {}
                try:
                    with winreg.OpenKey(parent, name) as sub:
                        j = 0
                        while j < 200:
                            try:
                                vname, vdata, _t = winreg.EnumValue(sub, j)
                            except OSError:
                                break
                            j += 1
                            if isinstance(vdata, str):
                                values[vname] = vdata
                except OSError:
                    continue
                out.append((name, values))
    return out


def reg_stamp(key: str):
    """Letzte Änderung eines ``HKLM``-Schlüssels (ändert sich, wenn Unterschlüssel dazukommen oder wegfallen)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return winreg.QueryInfoKey(k)[2]
    except (ImportError, OSError):
        return None


# ---------- Änderungen erkennen ----------

def signature(idx: GameIndex, steam: str | None = None, epic_dir: str = EPIC_MANIFESTS, stat=os.stat,
              regstamp=None) -> tuple:
    """Änderungszeiten der Manifest-Ordner (neue/entfernte/geänderte Manifeste ändern die Ordnerzeit) und der
    Steam-Wurzel – billig genug für einen Takt von wenigen Sekunden."""
    paths = list(idx.watch) or [epic_dir]
    root = steam if steam is not None else steam_root()
    out = [("root", root or "")]
    for path in paths:
        try:
            out.append((path, stat(path).st_mtime_ns))
        except OSError:
            out.append((path, None))
    out += [("reg:" + key, (regstamp or reg_stamp)(key)) for key in idx.watch_reg]
    return tuple(out)


# ---------- Rückfall: Prozess-Abstammung ----------

def is_helper(name: str) -> bool:
    low = name.lower()
    return low in LAUNCHER_HELPERS or any(word in low for word in _HELPER_WORDS)


class LaunchTracker:
    """Merkt sich, welche Prozesse (PID) ein Launcher direkt oder indirekt gestartet hat."""

    def __init__(self) -> None:
        self.launched: dict[int, str] = {}   # PID -> Launcher (Dateiname)
        self.seen: set[int] = set()

    def update(self, snap: dict[int, tuple[int, str]]) -> None:
        """``snap``: PID -> (Eltern-PID, Dateiname). Neue Prozesse erben die Markierung des Elternprozesses bzw.
        bekommen sie, wenn der Elternprozess ein Launcher ist; beendete Prozesse fallen danach heraus."""
        new = [pid for pid in snap if pid not in self.seen]
        changed = True
        while changed and new:                       # Eltern vor Kindern, auch wenn beide neu sind
            changed = False
            for pid in list(new):
                ppid, name = snap[pid]
                low = name.lower()
                parent = snap.get(ppid)
                launcher = parent[1].lower() if parent and parent[1].lower() in LAUNCHERS else self.launched.get(ppid)
                if launcher is None and ppid in snap and ppid in new:
                    continue                         # Elternteil erst prüfen
                if launcher and low not in LAUNCHERS and not is_helper(low):
                    self.launched[pid] = launcher
                new.remove(pid)
                self.seen.add(pid)
                changed = True
        self.seen.update(new)                        # Zyklen (PID-Wiederverwendung): nicht markieren
        alive = set(snap)
        self.launched = {pid: l for pid, l in self.launched.items() if pid in alive}
        self.seen &= alive

    def launcher_of(self, pid: int) -> str | None:
        return self.launched.get(pid)


def exe_game_key(exe: str) -> str:
    return "game:exe:" + norm(long_path(exe))


@functools.lru_cache(maxsize=1024)
def long_path(path: str) -> str:
    """Kurze 8.3-Namen (``C:\\PROGRA~1\\…``) in die lange Form – so starten manche Spiele Hilfsprogramme."""
    if "~" not in path or os.name != "nt":
        return path
    import ctypes
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetLongPathNameW(path, buf, len(buf))
        return buf.value if 0 < n < len(buf) else path
    except (OSError, AttributeError):
        return path


@functools.lru_cache(maxsize=512)
def exe_name(exe: str) -> str:
    """Anzeigename eines Spiels aus den Versionsinformationen (ProductName, sonst FileDescription), sonst der
    Dateiname ohne Endung."""
    for field_name in ("ProductName", "FileDescription"):
        value = _version_string(exe, field_name).strip()
        if value and value.lower() not in {"", "game", "launcher"}:
            return value[:255]
    base = ntpath.basename(exe)
    return (base[:-4] if base.lower().endswith(".exe") else base)[:255]


def _version_string(exe: str, name: str) -> str:
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes as w
    try:
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(exe, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe, 0, size, buf):
            return ""
        ptr, length = ctypes.c_void_p(), w.UINT()
        if not ver.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)) \
                or length.value < 4:
            return ""
        lang, cp = ctypes.cast(ptr, ctypes.POINTER(w.WORD * 2)).contents
        if not ver.VerQueryValueW(buf, rf"\StringFileInfo\{lang:04x}{cp:04x}\{name}", ctypes.byref(ptr),
                                  ctypes.byref(length)) or not length.value:
            return ""
        return ctypes.wstring_at(ptr, length.value).rstrip("\0")
    except (OSError, AttributeError):
        return ""
