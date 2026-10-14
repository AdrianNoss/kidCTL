"""Programmzeit: wie lange Apps aus dem Katalog während gezählter Bildschirmzeit geöffnet waren.

Gezählt wird je (User, Tag, App) einmal pro Takt, wenn mindestens ein Prozess der App läuft
(parallel geöffnete Apps zählen jeweils voll – „geöffnet“, nicht „im Vordergrund“).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

from . import store
from .procs import ProcInfo
from .programs import GENERIC_DIRS, STEAM_SYSTEM, _program_path

APPTIME_FILE = "apptime.json"
KEEP_DAYS = 35


class AppIndex:
    """Zuordnung Prozess -> App-Schlüssel aus dem Katalog."""

    def __init__(self, keys: set[str]) -> None:
        self.keys = keys
        self.exes = {k[4:]: k for k in keys if k.startswith("exe:")}
        self.dirs = {os.path.dirname(e): k for e, k in self.exes.items()
                     if os.path.dirname(e) not in GENERIC_DIRS}
        self.steam = next((k for e, k in self.exes.items() if e.startswith(STEAM_SYSTEM) or e == "/usr/bin/steam"),
                          None)

    def key_for(self, info: ProcInfo, steam_root: str = "") -> str | None:
        if info.flatpak_id:
            k = f"flatpak:{info.flatpak_id}"
            return k if k in self.keys else None
        path = _program_path(info)
        if path in self.exes:
            return self.exes[path]
        for d, k in self.dirs.items():
            if path.startswith(d + "/"):
                return k
        if self.steam and steam_root and path.startswith(steam_root):
            return self.steam
        return None


class AppTime:
    def __init__(self) -> None:
        self.local: dict[tuple[str, str, str], float] = {}

    @classmethod
    def load(cls) -> AppTime:
        a = cls()
        for row in (store.load(APPTIME_FILE) or {}).get("entries", []):
            a.local[(row["user"], row["day"], row["key"])] = float(row["seconds"])
        return a

    def save(self) -> None:
        store.save(APPTIME_FILE, {"entries": [{"user": u, "day": d, "key": k, "seconds": round(s, 1)}
                                              for (u, d, k), s in sorted(self.local.items())]})

    def add(self, user: str, day: date, keys: set[str], seconds: float) -> None:
        if seconds <= 0:
            return
        d = day.isoformat()
        for k in keys:
            self.local[(user, d, k)] = self.local.get((user, d, k), 0.0) + seconds

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.local = {k: v for k, v in self.local.items() if k[1] >= cutoff}

    def report_entries(self, today: date) -> list[tuple[str, date, str, int]]:
        days = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
        return [(u, date.fromisoformat(d), k, int(s)) for (u, d, k), s in sorted(self.local.items())
                if d in days and int(s) > 0]
