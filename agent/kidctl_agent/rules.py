"""Reine Entscheidungslogik: Darf ein Kind gerade nutzen und wie lange noch?

Bewusst ohne I/O, damit Agent und PAM-Prüfung dieselbe Logik nutzen und sie testbar ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from kidctl_common.protocol import ChildPolicy

# Gründe, warum die Nutzung nicht (mehr) erlaubt ist
QUOTA = "quota"                    # Tageskontingent aufgebraucht
WINDOW_NOT_OPEN = "window_not_open"
WINDOW_CLOSED = "window_closed"
LOCKED = "locked"                  # von den Eltern gesperrt („Jetzt sperren“)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining_seconds: int   # bis Kontingent- oder Fensterende, 0 wenn nicht erlaubt
    reason: str | None = None
    grace_seconds: int | None = None  # Vorwarnzeit bis zur Abmeldung (None = Standard)


def _seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def day_limits(child: ChildPolicy, day: date) -> tuple[int, time, time]:
    """Kontingent (Sekunden) und Zeitfenster für ``day`` inkl. Tagesausnahme."""
    rule = next((r for r in child.rules if r.weekday == day.weekday()), None)
    if rule is None:  # keine Regel -> nichts erlaubt (sicherer Standard)
        return 0, time(0), time(0)
    quota = rule.quota_minutes * 60
    end = rule.window_end
    for exc in child.exceptions:
        if exc.day == day:
            quota += exc.extra_minutes * 60
            if exc.window_end_override is not None:
                end = exc.window_end_override
    return quota, rule.window_start, end


def game_limit(child: ChildPolicy, day: date) -> int | None:
    """Spiele-Limit (Sekunden) für ``day`` inkl. Spielzeit-Gutschrift; ``None`` = kein Limit."""
    rule = next((r for r in child.rules if r.weekday == day.weekday()), None)
    if rule is None or rule.game_minutes is None:
        return None
    return (rule.game_minutes + sum(e.extra_game_minutes for e in child.exceptions if e.day == day)) * 60


def is_locked(child: ChildPolicy, now: datetime) -> bool:
    if not child.locked:
        return False
    if child.locked_until is None:
        return True
    until = child.locked_until if child.locked_until.tzinfo else child.locked_until.replace(tzinfo=timezone.utc)
    return (now if now.tzinfo else now.astimezone()) < until


def evaluate(child: ChildPolicy, now_local: datetime, used_seconds: int) -> Decision:
    """``now_local`` in der Zeitzone der Policy, ``used_seconds`` = Nutzung heute (alle Geräte)."""
    if is_locked(child, now_local):
        return Decision(False, 0, LOCKED, grace_seconds=child.lock_grace_seconds)
    quota, start, end = day_limits(child, now_local.date())
    now_s = _seconds(now_local.time())
    if now_s < _seconds(start):
        return Decision(False, 0, WINDOW_NOT_OPEN)
    if now_s >= _seconds(end):
        return Decision(False, 0, WINDOW_CLOSED)
    left_quota = quota - used_seconds
    if left_quota <= 0:
        return Decision(False, 0, QUOTA)
    return Decision(True, min(left_quota, _seconds(end) - now_s))


REASON_TEXT = {
    LOCKED: "Deine Eltern haben den Rechner gesperrt.",
    QUOTA: "Bildschirmzeit für heute aufgebraucht.",
    WINDOW_NOT_OPEN: "Die erlaubte Nutzungszeit hat noch nicht begonnen.",
    WINDOW_CLOSED: "Die erlaubte Nutzungszeit ist für heute vorbei.",
}
