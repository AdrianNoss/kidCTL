"""Limit je Programm/Kategorie (K4, ergänzt 2026-11-25): eigenes Tageskontingent für ein Programm (App-Schlüssel)
oder eine feste Kategorie (``cat:…``). Server (Auswahl, Anzeige) und Agent (Zuordnung laufender Prozesse) nutzen
dieselben Regeln.

Zuordnung eines Prozesses: genau der App-Schlüssel (Steam: jede Plattform-Variante), bei Kategorien die
freedesktop-Kategorien des Katalog-Eintrags (Windows: vom Agenten geschätzt, siehe ``win.catalog``); Browser zusätzlich
am Programmnamen, auch ohne Katalog-Eintrag (portable Browser). Umbenannte Programme erkennt das nicht
(Restlücke, ``docs/limitations*``).
"""

from __future__ import annotations

from typing import Callable, Iterable

from .freeapps import is_browser_name, program_name
from .i18n import N_
from .protocol import is_steam_key

MAX_LIMITS = 20          # Limits je Kind
MAX_MINUTES = 24 * 60

# Schlüssel -> (Bezeichnung, freedesktop-Kategorien)
CATEGORIES: dict[str, tuple[str, frozenset[str]]] = {
    "cat:browser": (N_("Browser"), frozenset({"WebBrowser"})),
    "cat:chat": (N_("Chat"), frozenset({"InstantMessaging", "Chat"})),
    "cat:media": (N_("Musik & Video"), frozenset({"AudioVideo", "Audio", "Video"})),
}


def is_category(key: str) -> bool:
    return key in CATEGORIES


def category_label(key: str) -> str:
    """Unübersetzte Bezeichnung (mit ``_()`` übersetzen)."""
    return CATEGORIES[key][0]


def matcher(limit_keys: Iterable[str], categories: dict[str, Iterable[str]],
            norm: Callable[[str], str] = lambda k: k) -> Callable[..., set[str]]:
    """``match(app_key, exe, browser=None)`` -> Limits, zu denen ein Prozess gehört. ``app_key`` = Katalog-Schlüssel
    des Prozesses (``None`` = keiner), ``exe`` = Programmdatei, ``browser`` = Browser laut Plattform (``None``: am
    Namen von ``exe`` erkennen); ``categories`` = Kategorien je App-Schlüssel laut Katalog; ``norm`` vergleicht
    Schlüssel (Windows: Groß-/Kleinschreibung egal)."""
    limits = list(dict.fromkeys(limit_keys))
    apps = {norm(k): k for k in limits if not is_category(k) and not is_steam_key(k)}
    steam = [k for k in limits if is_steam_key(k)]
    cats = [(k, CATEGORIES[k][1]) for k in limits if is_category(k)]
    cat_of = {norm(k): set(v) for k, v in categories.items()}

    def match(app_key: str | None, exe: str, browser: bool | None = None) -> set[str]:
        out: set[str] = set()
        if app_key is not None:
            n = norm(app_key)
            if n in apps:
                out.add(apps[n])
            if steam and is_steam_key(app_key):
                out.update(steam)
            own = cat_of.get(n, set())
            out.update(k for k, wanted in cats if own & wanted)
        if browser is None:
            browser = bool(exe) and is_browser_name(program_name(exe))
        if browser and "cat:browser" in limits:
            out.add("cat:browser")
        return out
    return match
