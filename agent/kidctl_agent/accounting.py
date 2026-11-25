"""Nutzungszeit-Zählung.

Dauer wird über ``CLOCK_BOOTTIME`` gemessen (unabhängig von Änderungen der Systemuhr); die
Systemuhr bestimmt nur den Kalendertag. Gezählt wird je Linux-User höchstens einmal pro Takt,
egal wie viele Sitzungen er hat.
"""

from __future__ import annotations

import sys
import time as _time
from datetime import date, timedelta

from . import store

# Größere Lücken zwischen zwei Takten (Suspend, hängender Prozess) werden nicht gezählt:
# CLOCK_BOOTTIME läuft im Suspend weiter, die Sitzung war in der Zeit aber nicht nutzbar.
MAX_GAP = 30.0  # Sekunden
KEEP_DAYS = 8


if sys.platform == "win32":
    import ctypes

    def boottime() -> float:
        """Windows: monotone Uhr ohne Ruhezustand (``QueryUnbiasedInterruptTime``, 100-ns-Schritte).
        Der Ruhezustand zählt so ohnehin nicht – wie die große Lücke (``MAX_GAP``) unter Linux."""
        t = ctypes.c_ulonglong()
        ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(t))
        return t.value / 1e7
else:
    def boottime() -> float:
        return _time.clock_gettime(_time.CLOCK_BOOTTIME)


class Usage:
    """Lokale Zählerstände je (User, Tag) und gemeldete Nutzung anderer Geräte je (Kind, Tag)."""

    def __init__(self) -> None:
        self.local: dict[tuple[str, str], float] = {}
        self.others: dict[tuple[int, str], int] = {}
        # Spielzeit (Teil der Nutzung, während ein Spiel läuft) – lokal je (User, Tag), andere Geräte je (Kind, Tag)
        self.games: dict[tuple[str, str], float] = {}
        self.others_games: dict[tuple[int, str], int] = {}
        # Limits je Programm/Kategorie: lokal je (User, Tag) {Limit: Sekunden}, andere Geräte je (Kind, Tag)
        self.limits: dict[tuple[str, str], dict[str, float]] = {}
        self.others_limits: dict[tuple[int, str], dict[str, int]] = {}
        self._last_tick: float | None = None

    # ---------- Persistenz ----------

    @classmethod
    def load(cls) -> Usage:
        return store.load(store.USAGE_FILE, cls._parse) or cls()

    @classmethod
    def _parse(cls, data: dict) -> Usage:
        u = cls()
        for key, sec in data.get("local", {}).items():
            user, day = key.split("|", 1)
            u.local[(user, day)] = float(sec)
        for key, sec in data.get("others", {}).items():
            child, day = key.split("|", 1)
            u.others[(int(child), day)] = int(sec)
        for key, sec in data.get("games", {}).items():
            user, day = key.split("|", 1)
            u.games[(user, day)] = float(sec)
        for key, sec in data.get("others_games", {}).items():
            child, day = key.split("|", 1)
            u.others_games[(int(child), day)] = int(sec)
        for key, per in data.get("limits", {}).items():
            user, day = key.split("|", 1)
            u.limits[(user, day)] = {str(k): float(v) for k, v in per.items()}
        for key, per in data.get("others_limits", {}).items():
            child, day = key.split("|", 1)
            u.others_limits[(int(child), day)] = {str(k): int(v) for k, v in per.items()}
        return u

    def save(self) -> None:
        store.save(store.USAGE_FILE, {
            "local": {f"{u}|{d}": round(s, 1) for (u, d), s in self.local.items()},
            "others": {f"{c}|{d}": s for (c, d), s in self.others.items()},
            "games": {f"{u}|{d}": round(s, 1) for (u, d), s in self.games.items()},
            "others_games": {f"{c}|{d}": s for (c, d), s in self.others_games.items()},
            "limits": {f"{u}|{d}": {k: round(v, 1) for k, v in per.items()} for (u, d), per in self.limits.items()},
            "others_limits": {f"{c}|{d}": per for (c, d), per in self.others_limits.items()},
        })

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.local = {k: v for k, v in self.local.items() if k[1] >= cutoff}
        self.others = {k: v for k, v in self.others.items() if k[1] >= cutoff}
        self.games = {k: v for k, v in self.games.items() if k[1] >= cutoff}
        self.others_games = {k: v for k, v in self.others_games.items() if k[1] >= cutoff}
        self.limits = {k: v for k, v in self.limits.items() if k[1] >= cutoff}
        self.others_limits = {k: v for k, v in self.others_limits.items() if k[1] >= cutoff}

    # ---------- Zählen ----------

    def tick(self, counting_users: set[str], day: date, now: float | None = None) -> float:
        """Rechnet die seit dem letzten Takt vergangene Zeit den aktiven Usern an.

        Gibt die angerechnete Dauer zurück (0 beim ersten Takt oder nach großer Lücke).
        """
        now = boottime() if now is None else now
        last, self._last_tick = self._last_tick, now
        if last is None:
            return 0.0
        delta = now - last
        if delta <= 0 or delta > MAX_GAP:
            return 0.0
        for user in counting_users:
            key = (user, day.isoformat())
            self.local[key] = self.local.get(key, 0.0) + delta
        return delta

    def add_games(self, user: str, day: date, seconds: float) -> None:
        """Spielzeit anrechnen (je Takt einmal, egal wie viele Spiele parallel laufen)."""
        if seconds > 0:
            key = (user, day.isoformat())
            self.games[key] = self.games.get(key, 0.0) + seconds

    def add_limits(self, user: str, day: date, keys: set[str], seconds: float) -> None:
        """Zeit je Limit anrechnen (je Takt einmal je Limit, egal wie viele passende Programme laufen)."""
        if seconds > 0 and keys:
            per = self.limits.setdefault((user, day.isoformat()), {})
            for key in keys:
                per[key] = per.get(key, 0.0) + seconds

    # ---------- Auswertung ----------

    def used_seconds(self, child_id: int, usernames: list[str], day: date) -> int:
        """Nutzung des Kindes an ``day``: alle zugeordneten User hier + andere Geräte."""
        d = day.isoformat()
        local = sum(self.local.get((u, d), 0.0) for u in usernames)
        return int(local) + self.others.get((child_id, d), 0)

    def used_game_seconds(self, child_id: int, usernames: list[str], day: date) -> int:
        d = day.isoformat()
        return int(sum(self.games.get((u, d), 0.0) for u in usernames)) + self.others_games.get((child_id, d), 0)

    def used_limit_seconds(self, child_id: int, usernames: list[str], day: date, key: str) -> int:
        d = day.isoformat()
        local = sum(self.limits.get((u, d), {}).get(key, 0.0) for u in usernames)
        return int(local) + self.others_limits.get((child_id, d), {}).get(key, 0)

    def limit_entries(self, user: str, day: date) -> dict[str, int]:
        return {k: int(v) for k, v in sorted(self.limits.get((user, day.isoformat()), {}).items())[:20]}

    def report_entries(self, today: date) -> list[tuple[str, date, int, int]]:
        """Zählerstände (Nutzung, Spielzeit) von gestern und heute für ``usage_report``."""
        days = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
        keys = sorted({k for k in self.local if k[1] in days} | {k for k in self.limits if k[1] in days})
        return [(u, date.fromisoformat(d), int(self.local.get((u, d), 0.0)), int(self.games.get((u, d), 0.0)))
                for u, d in keys]
