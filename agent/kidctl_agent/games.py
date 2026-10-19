"""Gespieltes Spiel erkennen (Steam, Heroic) – für Programmzeit je Spiel und „spielt gerade“.

- Steam setzt in jedem Spielprozess ``SteamAppId`` (auch unter Proton); Name aus
  ``<bibliothek>/steamapps/appmanifest_<id>.acf``.
- Heroic setzt ``HEROIC_APP_NAME`` (Spiel-ID) und ``HEROIC_APP_SOURCE`` (gog, epic, amazon, sideload);
  Name aus Heroics Bibliotheksdaten im Home (``store_cache/*_library.json``, ``sideload_apps``).

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
HEROIC_SOURCES = {"gog", "epic", "amazon", "sideload", "legendary", "nile"}
MAX_MANIFEST = 256 * 1024
MAX_LIBRARY = 32 * 1024 * 1024
MAX_NAME = 100
HEROIC_LIBRARIES = (".config/heroic/store_cache/gog_library.json", ".config/heroic/store_cache/legendary_library.json",
                    ".config/heroic/store_cache/nile_library.json", ".config/heroic/sideload_apps/library.json",
                    ".config/heroic/legendaryConfig/legendary/installed.json")


def read_small(path: str, limit: int) -> bytes | None:
    """Reguläre Datei ohne Symlink-Verfolgung lesen (``None`` bei Fehler, Symlink, FIFO, zu groß)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
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
