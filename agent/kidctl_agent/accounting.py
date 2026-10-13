"""Nutzungszeit-Zählung.

Dauer wird über ``CLOCK_BOOTTIME`` gemessen (unabhängig von Änderungen der Systemuhr); die
Systemuhr bestimmt nur den Kalendertag. Gezählt wird je Linux-User höchstens einmal pro Takt,
egal wie viele Sitzungen er hat.
"""

from __future__ import annotations

import time as _time
from datetime import date, timedelta

from . import store

# Größere Lücken zwischen zwei Takten (Suspend, hängender Prozess) werden nicht gezählt:
# CLOCK_BOOTTIME läuft im Suspend weiter, die Sitzung war in der Zeit aber nicht nutzbar.
MAX_GAP = 30.0  # Sekunden
KEEP_DAYS = 8


def boottime() -> float:
    return _time.clock_gettime(_time.CLOCK_BOOTTIME)


class Usage:
    """Lokale Zählerstände je (User, Tag) und gemeldete Nutzung anderer Geräte je (Kind, Tag)."""

    def __init__(self) -> None:
        self.local: dict[tuple[str, str], float] = {}
        self.others: dict[tuple[int, str], int] = {}
        self._last_tick: float | None = None

    # ---------- Persistenz ----------

    @classmethod
    def load(cls) -> Usage:
        u = cls()
        data = store.load(store.USAGE_FILE) or {}
        for key, sec in data.get("local", {}).items():
            user, day = key.split("|", 1)
            u.local[(user, day)] = float(sec)
        for key, sec in data.get("others", {}).items():
            child, day = key.split("|", 1)
            u.others[(int(child), day)] = int(sec)
        return u

    def save(self) -> None:
        store.save(store.USAGE_FILE, {
            "local": {f"{u}|{d}": round(s, 1) for (u, d), s in self.local.items()},
            "others": {f"{c}|{d}": s for (c, d), s in self.others.items()},
        })

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.local = {k: v for k, v in self.local.items() if k[1] >= cutoff}
        self.others = {k: v for k, v in self.others.items() if k[1] >= cutoff}

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

    # ---------- Auswertung ----------

    def used_seconds(self, child_id: int, usernames: list[str], day: date) -> int:
        """Nutzung des Kindes an ``day``: alle zugeordneten User hier + andere Geräte."""
        d = day.isoformat()
        local = sum(self.local.get((u, d), 0.0) for u in usernames)
        return int(local) + self.others.get((child_id, d), 0)

    def report_entries(self, today: date) -> list[tuple[str, date, int]]:
        """Zählerstände von gestern und heute für ``usage_report``."""
        days = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
        return [(u, date.fromisoformat(d), int(s)) for (u, d), s in sorted(self.local.items())
                if d in days]
