"""Reine Entscheidungslogik: Darf ein Kind gerade nutzen und wie lange noch?

Bewusst ohne I/O, damit Agent und PAM-Prüfung dieselbe Logik nutzen und sie testbar ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from kidctl_common.i18n import N_
from kidctl_common.protocol import Block, ChildPolicy

# Gründe, warum die Nutzung nicht (mehr) erlaubt ist
QUOTA = "quota"                    # Tageskontingent aufgebraucht
WINDOW_NOT_OPEN = "window_not_open"
WINDOW_CLOSED = "window_closed"
LOCKED = "locked"                  # von den Eltern gesperrt („Jetzt sperren“)
BLOCKED = "blocked"                # Sperrzeit (z. B. Schule)

DAY = 24 * 3600


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining_seconds: int   # bis Kontingent- oder Fensterende, 0 wenn nicht erlaubt
    reason: str | None = None
    grace_seconds: int | None = None  # Vorwarnzeit bis zur Abmeldung (None = Standard)
    block: Block | None = None        # laufende Sperrzeit (reason == BLOCKED)
    until: time | None = None         # Ende des erlaubten Abschnitts (Sperrzeit, Rahmenende; ggf. am Folgetag)


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


def _rule(child: ChildPolicy, day: date):
    return next((r for r in child.rules if r.weekday == day.weekday()), None)


def _frame(child: ChildPolicy, day: date) -> tuple[int, int] | None:
    """Rahmen von ``day`` in Sekunden ab ``day`` 00:00; Ende über Mitternacht > 24 h. None = kein Rahmen."""
    if _rule(child, day) is None:
        return None
    _quota, start, end = day_limits(child, day)
    s, e = _seconds(start), _seconds(end)
    if e == s:
        return None
    return s, e if e > s else e + DAY


def _segments(child: ChildPolicy, day: date) -> list[tuple[int, int]]:
    """Erlaubte Abschnitte (Sekunden ab ``day`` 00:00): Rest des Vortagsrahmens nach Mitternacht sowie der Rahmen
    des Tages ohne seine Sperrzeiten. Nach Beginn sortiert und zusammengefasst."""
    out = []
    prev = _frame(child, day - timedelta(days=1))
    if prev and prev[1] > DAY:
        out.append((0, prev[1] - DAY))
    frame = _frame(child, day)
    if frame:
        cuts = sorted((_seconds(b.start), _seconds(b.end)) for b in _rule(child, day).blocks if b.start < b.end)
        pos = frame[0]
        for bs, be in cuts:
            if be <= pos or bs >= frame[1]:
                continue
            if bs > pos:
                out.append((pos, bs))
            pos = max(pos, be)
        if pos < frame[1]:
            out.append((pos, frame[1]))
    merged: list[tuple[int, int]] = []
    for a, b in sorted(out):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def evaluate(child: ChildPolicy, now_local: datetime, used_seconds: int) -> Decision:
    """``now_local`` in der Zeitzone der Policy, ``used_seconds`` = Nutzung heute (alle Geräte)."""
    if is_locked(child, now_local):
        return Decision(False, 0, LOCKED, grace_seconds=child.lock_grace_seconds)
    day = now_local.date()
    now_s = _seconds(now_local.time())
    seg = next(((a, b) for a, b in _segments(child, day) if a <= now_s < b), None)
    if seg is None:
        rule, frame = _rule(child, day), _frame(child, day)
        if frame and frame[0] <= now_s < frame[1]:
            block = next((b for b in rule.blocks if _seconds(b.start) <= now_s < _seconds(b.end)), None)
            return Decision(False, 0, BLOCKED, block=block)
        if frame and now_s < frame[0]:
            return Decision(False, 0, WINDOW_NOT_OPEN)
        return Decision(False, 0, WINDOW_CLOSED)
    quota, _start, _end = day_limits(child, day)
    left_quota = quota - used_seconds
    if left_quota <= 0:
        return Decision(False, 0, QUOTA)
    end = seg[1] % DAY
    return Decision(True, min(left_quota, seg[1] - now_s), until=time(end // 3600, end % 3600 // 60, end % 60))


def blocks_on(child: ChildPolicy, day: date) -> list[Block]:
    rule = _rule(child, day)
    return sorted(rule.blocks, key=lambda b: b.start) if rule else []


REASON_TEXT = {   # mit N_ markiert, Übersetzung bei der Anzeige
    LOCKED: N_("Deine Eltern haben den Rechner gesperrt."),
    QUOTA: N_("Bildschirmzeit für heute aufgebraucht."),
    WINDOW_NOT_OPEN: N_("Die erlaubte Nutzungszeit hat noch nicht begonnen."),
    WINDOW_CLOSED: N_("Die erlaubte Nutzungszeit ist für heute vorbei."),
    BLOCKED: N_("Jetzt ist Sperrzeit."),
}
