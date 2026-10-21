"""GNOME-Suche: Suchanbieter nicht freigegebener Apps für Kinder abschalten.

Tippt das Kind in der GNOME-Übersicht, startet GNOME Shell die Suchanbieter der Apps per D-Bus (Dateien,
Kalender, Uhren …). Bei nicht freigegebenen Apps sperrt der Agent diese Prozesse – mit einer Benachrichtigung
je App und Suche. Der Agent trägt diese Anbieter deshalb in die Einstellung des Kindes
``org.gnome.desktop.search-providers disabled`` ein (als Kind per gsettings) und nimmt sie nach einer Freigabe
wieder heraus. Einträge, die das Kind selbst gesetzt hat, bleiben unberührt. Geschrieben wird nur bei
laufender GNOME-Sitzung und nur, wenn sich die Menge geändert hat (Zustand in ``gnome-search.json``).
"""

from __future__ import annotations

import ast
import configparser
import logging
import subprocess
from pathlib import Path

from . import store
from .apps import APP_DIRS
from .desktoptrust import Target, entry_target

log = logging.getLogger("kidctl.gnomesearch")

PROVIDER_DIR = Path("/usr/share/gnome-shell/search-providers")
SCHEMA, KEY = "org.gnome.desktop.search-providers", "disabled"
STATE_FILE = "gnome-search.json"   # User -> vom Agenten eingetragene Desktop-IDs
MAX_SIZE = 64 * 1024


def providers(provider_dir: Path = PROVIDER_DIR, app_dirs=APP_DIRS) -> dict[str, Target]:
    """Desktop-ID -> was die App startet (nur Anbieter, deren App auflösbar ist)."""
    out: dict[str, Target] = {}
    try:
        files = sorted(provider_dir.glob("*.ini"))
    except OSError:
        return out
    for f in files:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read_string(f.read_text(errors="replace")[:MAX_SIZE])
            desktop_id = cp["Shell Search Provider"]["DesktopId"].strip()
        except (OSError, configparser.Error, KeyError):
            continue
        for d in app_dirs:
            try:
                text = (Path(d) / desktop_id).read_text(errors="replace")[:MAX_SIZE]
            except OSError:
                continue
            if target := entry_target(text):
                out[desktop_id] = target
            break
    return out


def parse_strv(text: str) -> list[str]:
    """Ausgabe von ``gsettings get`` (``@as []`` bzw. ``['a', 'b']``) -> Liste."""
    text = text.strip()
    if text.startswith("@as "):
        text = text[4:]
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def format_strv(values) -> str:
    return "[" + ", ".join(repr(v) for v in sorted(values)) + "]" if values else "@as []"


class GnomeSearch:
    def __init__(self, as_user, run=subprocess.run) -> None:
        """``as_user(user, uid, *cmd)`` -> Befehlszeile im Kontext des Users (``None`` ohne Session-Bus)."""
        self.as_user, self.run = as_user, run
        self.managed: dict[str, list[str]] = store.load(STATE_FILE) or {}

    def _gsettings(self, user: str, uid: int, *args: str) -> str | None:
        cmd = self.as_user(user, uid, "gsettings", *args)
        if cmd is None:
            return None
        try:
            r = self.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout if r.returncode == 0 else None

    def sync(self, user: str, uid: int, blocked: set[str]) -> None:
        """Anbieter ``blocked`` für ``user`` abschalten, früher abgeschaltete, jetzt erlaubte wieder ein."""
        before = set(self.managed.get(user, []))
        if blocked == before:
            return
        current = self._gsettings(user, uid, "get", SCHEMA, KEY)
        if current is None:
            return   # keine Sitzung/kein dconf – beim nächsten Abgleich erneut
        values = set(parse_strv(current))
        own = values - before                      # vom Kind selbst abgeschaltet
        wanted = own | blocked
        if wanted != values and self._gsettings(user, uid, "set", SCHEMA, KEY, format_strv(wanted)) is None:
            return
        log.info("GNOME-Suche für %s: abgeschaltet %s", user, ", ".join(sorted(blocked)) or "–")
        if blocked:
            self.managed[user] = sorted(blocked)
        else:
            self.managed.pop(user, None)
        store.save(STATE_FILE, self.managed)
