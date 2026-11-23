"""Gespieltes Spiel erkennen (Steam, Heroic, Lutris, Bottles) – für Programmzeit je Spiel und „spielt gerade“.

- Steam setzt in jedem Spielprozess ``SteamAppId`` (auch unter Proton); Name aus
  ``<bibliothek>/steamapps/appmanifest_<id>.acf``.
- Heroic setzt ``HEROIC_APP_NAME`` (Spiel-ID) und ``HEROIC_APP_SOURCE`` (gog, epic, amazon, sideload);
  Name aus Heroics Bibliotheksdaten im Home (``store_cache/*_library.json``, ``sideload_apps``).
- Lutris setzt ``GAME_NAME`` (Anzeigename) und ``LUTRIS_GAME_UUID`` (je Start neu) -> ``game:lutris:<Name>``.
- Bottles setzt nur ``BOTTLE`` und ``WINEPREFIX`` (Bottle-Ordner); das Programm ergibt sich aus argv[0] des
  Wine-Prozesses und der Programmliste in ``<WINEPREFIX>/bottle.yml`` -> ``game:bottles:<Bottle>:<Programm-ID>``.
  Wine-Hilfsprozesse und nicht eingetragene Programme zählen nicht.
  (Formate: echte Daten aus der Test-VM, ``agent/tests/fixtures/wine_launchers``.)

Alle Quellen gehören dem Kind: nur zur Anzeige, nie für Freigaben. Dateien werden ohne
Symlink-Verfolgung, nur als reguläre Datei und mit Größenlimit gelesen (kein Hängen an FIFOs).
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat

log = logging.getLogger("kidctl.games")

STEAM_ID = re.compile(r"^[1-9][0-9]{0,9}$")
HEROIC_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
LUTRIS_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_BOTTLE_YML = 1024 * 1024
HEROIC_SOURCES = {"gog", "epic", "amazon", "sideload", "legendary", "nile"}
MAX_MANIFEST = 256 * 1024
MAX_LIBRARY = 32 * 1024 * 1024
MAX_NAME = 100
HEROIC_LIBRARIES = (".config/heroic/store_cache/gog_library.json", ".config/heroic/store_cache/legendary_library.json",
                    ".config/heroic/store_cache/nile_library.json", ".config/heroic/sideload_apps/library.json",
                    ".config/heroic/legendaryConfig/legendary/installed.json")


def read_small(path: str, limit: int, uid: int | None = None) -> bytes | None:
    """Reguläre Datei ohne Symlink-Verfolgung lesen (``None`` bei Fehler, Symlink, FIFO, zu groß, fremder Besitzer)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit or (uid is not None and st.st_uid != uid):
            return None
        return os.read(fd, limit + 1)[:limit]
    except OSError:
        return None
    finally:
        os.close(fd)


def clean_name(name: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:MAX_NAME]


def game_id(env: dict[str, str]) -> tuple[str, str] | None:
    """(Schlüssel, Art) aus der Umgebung eines Prozesses, z. B. ("game:steam:620", "steam")."""
    app, source = env.get("HEROIC_APP_NAME", ""), env.get("HEROIC_APP_SOURCE", "").lower()
    if HEROIC_ID.match(app) and source in HEROIC_SOURCES:
        return f"game:heroic:{source}:{app}", "heroic"
    if LUTRIS_UUID.match(env.get("LUTRIS_GAME_UUID", "")) and (name := clean_name(env.get("GAME_NAME", ""))):
        return f"game:lutris:{name}", "lutris"
    sid = env.get("SteamAppId", "")
    if STEAM_ID.match(sid):
        return f"game:steam:{sid}", "steam"
    return None


class GameNames:
    """Spielnamen nachschlagen (mit Cache je Datei und Änderungszeit)."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[str, str]]] = {}

    def name(self, key: str, home: str, steam_lib: str) -> str:
        parts = key.split(":")
        if parts[1] == "steam":
            return self._steam(parts[2], steam_lib) or f"Steam-Spiel {parts[2]}"
        if parts[1] == "lutris":
            return key.split(":", 2)[2]
        if parts[1] == "bottles":
            return parts[2]   # Ersatz: Name aus der bottle.yml setzt der Agent beim Erkennen
        return self._heroic(parts[3], home) or parts[3]

    def _steam(self, appid: str, lib: str) -> str:
        raw = read_small(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"), MAX_MANIFEST)
        m = re.search(rb'^\s*"name"\s+"((?:[^"\\]|\\.)*)"', raw or b"", re.M)
        return clean_name(m.group(1).decode(errors="replace")) if m else ""

    def _heroic(self, app: str, home: str) -> str:
        for rel in HEROIC_LIBRARIES:
            if title := self._titles(os.path.join(home, rel)).get(app):
                return title
        return ""

    def _titles(self, path: str) -> dict[str, str]:
        try:
            mtime = os.lstat(path).st_mtime
        except OSError:
            return {}
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        titles: dict[str, str] = {}
        raw = read_small(path, MAX_LIBRARY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        _collect(data, titles)
        self._cache[path] = (mtime, titles)
        return titles


def _collect(data, out: dict[str, str]) -> None:
    """Listen von {app_name, title} bzw. {<id>: {title}} (legendary installed.json) einsammeln."""
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(v.get("title"), str) and HEROIC_ID.match(str(k)):
                out.setdefault(str(k), clean_name(v["title"]))
            elif isinstance(v, (list, dict)):
                _collect(v, out)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("app_name"), str) and isinstance(item.get("title"), str):
                out.setdefault(item["app_name"], clean_name(item["title"]))


def bottles_program(env: dict[str, str], argv: tuple[str, ...], uid: int) -> tuple[str, str] | None:
    """(Schlüssel, Name) des Bottles-Programms, das ein Wine-Prozess ausführt (``None``: kein eingetragenes)."""
    prefix, exe = env.get("WINEPREFIX", ""), (argv[0] if argv else "").replace("\\", "/").lower()
    if not env.get("BOTTLE") or not os.path.isabs(prefix) or not exe.endswith(".exe"):
        return None
    if exe[1:3] == ":/":                                   # C:\… -> <prefix>/drive_c/…
        exe = f"/drive_{exe[0]}{exe[2:]}"
    progs = bottles_programs(os.path.join(prefix, "bottle.yml"), uid)
    match = next((p for p in progs if p[2].lower().endswith(exe)), None) \
        or next((p for p in progs if p[2].lower().rsplit("/", 1)[-1] == exe.rsplit("/", 1)[-1]), None)
    if match is None:
        return None
    bottle = clean_name(os.path.basename(prefix.rstrip("/")))
    return f"game:bottles:{bottle}:{match[0]}", match[1] or bottle


def bottles_programs(path: str, uid: int) -> list[tuple[str, str, str]]:
    """Programmliste (ID, Name, Pfad) aus ``External_Programs`` einer bottle.yml (nur Datei des Kindes).

    Kein YAML-Paket: Bottles schreibt per PyYAML (Einrückung 4, Nicht-ASCII als ``\\xNN``, lange Werte in
    doppelten Anführungszeichen umbrochen); gelesen werden nur ``name`` und ``path`` je Programm."""
    raw = read_small(path, MAX_BOTTLE_YML, uid)
    if raw is None:
        return []
    progs: dict[str, dict[str, str]] = {}
    current, inside, lines = None, False, raw.decode(errors="replace").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            inside = line.rstrip() == "External_Programs:"
            continue
        if not inside:
            continue
        key, sep, value = line.strip().partition(":")
        if indent == 4 and sep and not value.strip():
            current = progs.setdefault(_scalar(key), {})
        elif indent == 8 and current is not None and key in ("name", "path"):
            value = value.strip()
            while value.startswith('"') and not _closed(value) and i < len(lines):   # umbrochener Wert
                value += " " + lines[i].strip()
                i += 1
            current[key] = _scalar(value)
    return [(clean_name(pid), clean_name(p.get("name", "")), p["path"]) for pid, p in progs.items() if p.get("path")]


def _closed(value: str) -> bool:
    body = value[1:]
    return re.search(r'(?<!\\)(?:\\\\)*"\s*$', body) is not None


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return value[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        except UnicodeDecodeError:
            return value[1:-1]
    return value
