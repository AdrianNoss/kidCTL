"""Programmzeit unter Windows: Welche App (Schlüssel) gehört ein Prozess? Grundlage für Programm- und Spielzeit,
„spielt gerade“ und das Spiele-Limit.

Reihenfolge: erkanntes Spiel (Steam/Epic-Ordner) -> Store-App (Package Family Name) -> Katalog-Programm (genauer
Pfad, sonst sein Programm- bzw. App-Ordner außerhalb von ``%WINDIR%``). Alles andere zählt nicht (Systemprozesse).
"""

from __future__ import annotations

from .catalog import Catalog, norm
from .games import GameIndex
from .programs import WINDIR, _under, app_root, key_norm


class WinAppIndex:
    def __init__(self, catalog: Catalog | None, games: GameIndex) -> None:
        self.games = games
        self.exact: dict[str, str] = {}
        self.dirs: list[tuple[str, str]] = []
        self.pfns: dict[str, str] = {}
        for app in (catalog.apps if catalog else []):
            if app.key.startswith("appx:"):
                self.pfns[app.key[5:].lower()] = app.key
            elif app.key.startswith("exe:") and "Shell" not in app.categories:
                path = norm(app.key[4:])
                self.exact[path] = app.key
                if not _under(path, WINDIR):
                    self.dirs.append((app_root(app.key[4:]), app.key))
        self.dirs.sort(key=lambda d: -len(d[0]))   # genauester Ordner zuerst

    def key_for(self, proc) -> tuple[str, str] | None:
        """(Schlüssel, Name) bzw. ``None``; Name nur bei erkannten Spielen (sonst aus dem Katalog)."""
        if game := self.games.game_of(proc.exe):
            return game
        if proc.pfn:
            key = self.pfns.get(proc.pfn.lower())
            return (key, "") if key else None
        path = norm(proc.exe)
        if key := self.exact.get(path):
            return key, ""
        for folder, key in self.dirs:
            if _under(path, folder):
                return key, ""
        return None


__all__ = ["WinAppIndex", "key_norm"]
