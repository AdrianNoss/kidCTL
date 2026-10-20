"""Bildschirmzeit: Wochenvorlage und Tagesausnahmen (Eingabe-Validierung, Gruppen)."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import settings
from .models import Child, DayException, WeekdayRule

WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
# Gruppen für „Werktage/Wochenende gleich“: Schlüssel -> (Bezeichnung, Wochentage)
GROUPS = {"wd": ("Werktage (Mo–Fr)", range(0, 5)), "we": ("Wochenende (Sa–So)", range(5, 7))}

MAX_QUOTA = 24 * 60
MAX_EXTRA = 12 * 60


class ScheduleError(ValueError):
    pass


def today_local() -> date:
    """Kalendertag in der Heimat-Zeitzone (Tageswechsel 00:00 Europe/Berlin)."""
    return datetime.now(ZoneInfo(settings.timezone)).date()


def parse_time(raw: str, label: str) -> time:
    try:
        return time.fromisoformat(raw.strip())
    except ValueError:
        raise ScheduleError(f"{label}: ungültige Uhrzeit „{raw}“.") from None


def parse_minutes(raw: str, label: str, maximum: int) -> int:
    raw = raw.strip()
    if not raw.isdigit() or int(raw) > maximum:
        raise ScheduleError(f"{label}: Minuten müssen zwischen 0 und {maximum} liegen.")
    return int(raw)


def parse_rule(form, prefix: str, label: str) -> tuple[int, time, time, int | None]:
    """(Kontingent, Fensterbeginn, Fensterende, Spiele-Limit oder None)."""
    quota = parse_minutes(str(form.get(f"quota_{prefix}", "")), label, MAX_QUOTA)
    start = parse_time(str(form.get(f"start_{prefix}", "")), label)
    end = parse_time(str(form.get(f"end_{prefix}", "")), label)
    if start >= end:
        raise ScheduleError(f"{label}: Fensterbeginn muss vor dem Fensterende liegen.")
    raw_games = str(form.get(f"games_{prefix}", "")).strip()
    games = parse_minutes(raw_games, f"{label} (Spiele)", MAX_QUOTA) if raw_games else None
    return quota, start, end, games


def rules_by_day(child: Child) -> dict[int, WeekdayRule]:
    return {r.weekday: r for r in child.rules}


def apply_schedule(child: Child, form) -> None:
    """Übernimmt das Formular in die Wochenvorlage. Wirft ScheduleError, ohne etwas zu ändern."""
    rules = rules_by_day(child)
    new: dict[int, tuple[int, time, time, int | None]] = {}
    if child.weekdays_same:
        for key, (label, days) in GROUPS.items():
            values = parse_rule(form, key, label)
            new.update({d: values for d in days})
    else:
        for d in range(7):
            new[d] = parse_rule(form, str(d), WEEKDAY_NAMES[d])
    for d, (quota, start, end, games) in new.items():
        rule = rules.get(d)
        if rule is None:
            rule = WeekdayRule(weekday=d)
            child.rules.append(rule)
        rule.quota_minutes, rule.window_start, rule.window_end, rule.game_minutes = quota, start, end, games


def group_differs(child: Child) -> list[str]:
    """Gruppen, deren Tage aktuell unterschiedliche Werte haben (Hinweis im Gruppenmodus)."""
    rules = rules_by_day(child)
    out = []
    for label, days in GROUPS.values():
        vals = {(rules[d].quota_minutes, rules[d].window_start, rules[d].window_end, rules[d].game_minutes)
                for d in days if d in rules}
        if len(vals) > 1:
            out.append(label)
    return out


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}:{m:02d} h"


def parse_credit(child: Child, day: str, extra_minutes: str, window_end: str,
                 extra_games: str = "0") -> tuple[date, int, time | None, int]:
    """Validiert eine Gutschrift (auch genehmigte/angepasste Verlängerungsanfrage).
    (Tag, Minuten, Fensterende, Spielzeit-Minuten)."""
    try:
        the_day = date.fromisoformat(day)
    except ValueError:
        raise ScheduleError("Ungültiges Datum.") from None
    extra = parse_minutes(extra_minutes or "0", "Zusätzliche Minuten", MAX_EXTRA)
    override = parse_time(window_end, "Fensterende") if window_end.strip() else None
    games = parse_minutes(extra_games or "0", "Zusätzliche Spielzeit", MAX_EXTRA)
    check_credit(child, the_day, extra, override, games)
    return the_day, extra, override, games


def check_credit(child: Child, the_day: date, extra: int, override: time | None, games: int = 0) -> None:
    if the_day < today_local():
        raise ScheduleError("Gutschriften sind nur für heute oder künftige Tage möglich.")
    if not extra and override is None and not games:
        raise ScheduleError("Bitte Minuten, Spielzeit und/oder ein späteres Fensterende angeben.")
    rule = rules_by_day(child).get(the_day.weekday())
    if games and (rule is None or rule.game_minutes is None):
        raise ScheduleError("Für diesen Tag ist kein Spiele-Limit eingestellt – Spielzeit-Gutschrift nicht nötig.")
    if override is not None and rule is not None and override <= rule.window_end:
        raise ScheduleError(
            f"Das Fensterende muss nach dem regulären Ende ({rule.window_end:%H:%M}) liegen.")


def add_credit(db, child: Child, the_day: date, extra: int, override: time | None, games: int = 0) -> None:
    """Tagesausnahme anlegen/erweitern: Minuten addieren, Fensterende ersetzen. Kein Commit."""
    exc = db.scalar(select(DayException).where(
        DayException.child_id == child.id, DayException.day == the_day))
    if exc is None:
        exc = DayException(child_id=child.id, day=the_day, extra_minutes=0, extra_game_minutes=0)
        db.add(exc)
    exc.extra_minutes = min(exc.extra_minutes + extra, MAX_QUOTA)
    exc.extra_game_minutes = min((exc.extra_game_minutes or 0) + games, MAX_QUOTA)
    if override is not None:
        exc.window_end_override = override
