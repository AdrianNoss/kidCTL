"""Programmzeit unter Windows: Welche App (Schlüssel) gehört ein Prozess? Grundlage für Programm- und Spielzeit,
„spielt gerade“ und das Spiele-Limit.

Reihenfolge: erkanntes Spiel (Steam/Epic-Ordner) -> Store-App (Package Family Name) -> Katalog-Programm (genauer
Pfad, sonst sein Programm- bzw. App-Ordner außerhalb von ``%WINDIR%``). Alles andere zählt nicht (Systemprozesse).
"""

from __future__ import annotations

from functools import lru_cache

from kidctl_common.freeapps import is_browser_name, name_never_free, program_name

from .catalog import Catalog, norm
from .games import MINECRAFT_JAVA, GameIndex, _version_string, package_runtime
from .programs import PROGRAM_FILES, PROGRAM_FILES_X86, WINDIR, _under, app_root, key_norm

# Für Kinder nicht beschreibbar: nur hier installierte Programme können „zählt nicht“ sein (im Benutzerordner könnte das
# Kind die Programmdatei z. B. gegen einen umbenannten Browser tauschen)
PROTECTED = (PROGRAM_FILES, PROGRAM_FILES_X86, WINDIR)


@lru_cache(maxsize=512)
def _original_name(exe: str) -> str:
    return _version_string(exe, "OriginalFilename")


def free_process_ok(proc, original=None) -> bool:
    """Darf ein Prozess, der einem „zählt nicht“-Programm zugeordnet ist, als solches gelten? Nur Store-Apps und
    Programme unter Programme/Windows, kein Browser/Terminal/Launcher – auch nicht laut Versionsinformation
    (``OriginalFilename``, gegen umbenannte Kopien)."""
    if name_never_free(program_name(proc.exe)):
        return False
    if proc.pfn:
        return True   # Paketordner unter WindowsApps gehört TrustedInstaller
    path = norm(proc.exe)
    if not any(_under(path, d) for d in PROTECTED):
        return False
    orig = (original or _original_name)(proc.exe)
    return not (orig and name_never_free(program_name(orig)))


def browser_process(proc) -> bool:
    """Browser (auch ohne Katalog-Eintrag, z. B. portabel im Benutzerordner)? Dann zählt die Zeit immer."""
    return is_browser_name(program_name(proc.exe))


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
        if not proc.pfn and package_runtime(norm(proc.exe)):
            return MINECRAFT_JAVA
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


__all__ = ["WinAppIndex", "browser_process", "free_process_ok", "key_norm"]
