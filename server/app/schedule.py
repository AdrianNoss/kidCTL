"""Bildschirmzeit: Wochenvorlage und Tagesausnahmen (Eingabe-Validierung, Gruppen)."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .config import settings
from .models import Child, WeekdayRule

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


def parse_rule(form, prefix: str, label: str) -> tuple[int, time, time]:
    quota = parse_minutes(str(form.get(f"quota_{prefix}", "")), label, MAX_QUOTA)
    start = parse_time(str(form.get(f"start_{prefix}", "")), label)
    end = parse_time(str(form.get(f"end_{prefix}", "")), label)
    if start >= end:
        raise ScheduleError(f"{label}: Fensterbeginn muss vor dem Fensterende liegen.")
    return quota, start, end


def rules_by_day(child: Child) -> dict[int, WeekdayRule]:
    return {r.weekday: r for r in child.rules}


def apply_schedule(child: Child, form) -> None:
    """Übernimmt das Formular in die Wochenvorlage. Wirft ScheduleError, ohne etwas zu ändern."""
    rules = rules_by_day(child)
    new: dict[int, tuple[int, time, time]] = {}
    if child.weekdays_same:
        for key, (label, days) in GROUPS.items():
            values = parse_rule(form, key, label)
            new.update({d: values for d in days})
    else:
        for d in range(7):
            new[d] = parse_rule(form, str(d), WEEKDAY_NAMES[d])
    for d, (quota, start, end) in new.items():
        rule = rules.get(d)
        if rule is None:
            rule = WeekdayRule(weekday=d)
            child.rules.append(rule)
        rule.quota_minutes, rule.window_start, rule.window_end = quota, start, end


def group_differs(child: Child) -> list[str]:
    """Gruppen, deren Tage aktuell unterschiedliche Werte haben (Hinweis im Gruppenmodus)."""
    rules = rules_by_day(child)
    out = []
    for label, days in GROUPS.values():
        vals = {(rules[d].quota_minutes, rules[d].window_start, rules[d].window_end)
                for d in days if d in rules}
        if len(vals) > 1:
            out.append(label)
    return out


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}:{m:02d} h"
