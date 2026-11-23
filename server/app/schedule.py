"""Bildschirmzeit: Wochenvorlage und Tagesausnahmen (Eingabe-Validierung, Gruppen)."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kidctl_common import protocol as p

from . import i18n
from .i18n import N_, _
from .config import settings
from .models import Child, DayException, VacationRule, WeekdayRule

WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
# Gruppen für „Werktage/Wochenende gleich“: Schlüssel -> (Bezeichnung, Wochentage)
GROUPS = {"wd": (N_("Werktage (Mo–Fr)"), range(0, 5)), "we": (N_("Wochenende (Sa–So)"), range(5, 7))}

MAX_QUOTA = 24 * 60
MAX_EXTRA = 12 * 60
MAX_BLOCKS = p.MAX_BLOCKS
OVERNIGHT_MAX_END = time(6)   # Fenster über Mitternacht: Ende spätestens 06:00 am Folgetag


class ScheduleError(ValueError):
    pass


def today_local() -> date:
    """Kalendertag in der Heimat-Zeitzone (Tageswechsel 00:00 Europe/Berlin)."""
    return datetime.now(ZoneInfo(settings.timezone)).date()


def parse_time(raw: str, label: str) -> time:
    try:
        return time.fromisoformat(raw.strip())
    except ValueError:
        raise ScheduleError(_("{label}: ungültige Uhrzeit „{raw}“.").format(label=label, raw=raw)) from None


def parse_minutes(raw: str, label: str, maximum: int) -> int:
    raw = raw.strip()
    if not raw.isdigit() or int(raw) > maximum:
        raise ScheduleError(_("{label}: Minuten müssen zwischen 0 und {max} liegen.").format(label=label, max=maximum))
    return int(raw)


def check_window(start: time, end: time, label: str) -> None:
    """Ende nach Beginn oder – über Mitternacht – Ende spätestens 06:00 am Folgetag."""
    if start == end or (end < start and end > OVERNIGHT_MAX_END):
        raise ScheduleError(_("{label}: Fensterbeginn muss vor dem Fensterende liegen (über Mitternacht: Ende "
                              "spätestens 06:00 Uhr).").format(label=label))


def end_after(start: time, end: time, other: time) -> bool:
    """Liegt ``other`` nach ``end`` – beide als Fensterende eines Fensters ab ``start`` (über Mitternacht später)?"""
    def key(t: time) -> tuple[int, time]:
        return (1 if t < start else 0, t)
    return key(other) > key(end)


def parse_blocks(form, prefix: str, label: str) -> list[p.Block]:
    blocks = []
    for i in range(MAX_BLOCKS):
        raw_start = str(form.get(f"block_{prefix}_{i}_start", "")).strip()
        raw_end = str(form.get(f"block_{prefix}_{i}_end", "")).strip()
        name = str(form.get(f"block_{prefix}_{i}_name", "")).strip()
        if not raw_start and not raw_end:
            continue   # leerer Platz (Name allein wird ignoriert)
        what = _("{label}: Sperrzeit {n}").format(label=label, n=i + 1)
        if not raw_start or not raw_end:
            raise ScheduleError(_("{label}: Beginn und Ende angeben.").format(label=what))
        start, end = parse_time(raw_start, what), parse_time(raw_end, what)
        if start >= end:
            raise ScheduleError(_("{label}: Beginn muss vor dem Ende liegen (nicht über Mitternacht).").format(
                label=what))
        if len(name) > 40:
            raise ScheduleError(_("{label}: Name höchstens 40 Zeichen.").format(label=what))
        blocks.append(p.Block(start=start, end=end, name=name))
    return sorted(blocks, key=lambda b: b.start)


def parse_rule(form, prefix: str, label: str) -> tuple[int, time, time, int | None, list[p.Block]]:
    """(Kontingent, Fensterbeginn, Fensterende, Spiele-Limit oder None, Sperrzeiten)."""
    quota = parse_minutes(str(form.get(f"quota_{prefix}", "")), label, MAX_QUOTA)
    start = parse_time(str(form.get(f"start_{prefix}", "")), label)
    end = parse_time(str(form.get(f"end_{prefix}", "")), label)
    check_window(start, end, label)
    raw_games = str(form.get(f"games_{prefix}", "")).strip()
    games = parse_minutes(raw_games, _("{label} (Spiele)").format(label=label), MAX_QUOTA) if raw_games else None
    return quota, start, end, games, parse_blocks(form, prefix, label)


def rules_by_day(child: Child, vacation: bool = False) -> dict:
    """Wochenvorlage bzw. Ferienvorlage je Wochentag (Ferienvorlage noch leer -> Wochenvorlage, z. B. zum Vorbelegen)."""
    return {r.weekday: r for r in (child.vacation_rules if vacation and child.vacation_rules else child.rules)}


def apply_schedule(child: Child, form, vacation: bool = False) -> None:
    """Übernimmt das Formular in die Wochen- bzw. Ferienvorlage. Wirft ScheduleError, ohne etwas zu ändern."""
    target, model = (child.vacation_rules, VacationRule) if vacation else (child.rules, WeekdayRule)
    rules = {r.weekday: r for r in target}
    new: dict[int, tuple[int, time, time, int | None, list[p.Block]]] = {}
    if child.weekdays_same:
        for key, (label, days) in GROUPS.items():
            values = parse_rule(form, key, _(label))
            new.update({d: values for d in days})
    else:
        for d in range(7):
            new[d] = parse_rule(form, str(d), i18n.weekday_names()[d])
    for d, (quota, start, end, games, blocks) in new.items():
        rule = rules.get(d)
        if rule is None:
            rule = model(weekday=d)
            target.append(rule)
        rule.quota_minutes, rule.window_start, rule.window_end, rule.game_minutes = quota, start, end, games
        rule.block_list = blocks


def group_differs(child: Child, vacation: bool = False) -> list[str]:
    """Gruppen, deren Tage aktuell unterschiedliche Werte haben (Hinweis im Gruppenmodus)."""
    rules = rules_by_day(child, vacation)
    out = []
    for label, days in GROUPS.values():
        vals = {(rules[d].quota_minutes, rules[d].window_start, rules[d].window_end, rules[d].game_minutes,
                 rules[d].blocks and tuple((b.start, b.end, b.name) for b in rules[d].block_list))
                for d in days if d in rules}
        if len(vals) > 1:
            out.append(label)
    return out


def fmt_minutes(minutes: int) -> str:
    """Dauer in der Sprache der laufenden Anfrage (de „1:20 h“, en „1 h 20 min“)."""
    return i18n.fmt_minutes(minutes)


def parse_credit(child: Child, day: str, extra_minutes: str, window_end: str,
                 extra_games: str = "0") -> tuple[date, int, time | None, int]:
    """Validiert eine Gutschrift (auch genehmigte/angepasste Verlängerungsanfrage).
    (Tag, Minuten, Fensterende, Spielzeit-Minuten)."""
    try:
        the_day = date.fromisoformat(day)
    except ValueError:
        raise ScheduleError(_("Ungültiges Datum.")) from None
    extra = parse_minutes(extra_minutes or "0", _("Zusätzliche Minuten"), MAX_EXTRA)
    override = parse_time(window_end, _("Fensterende")) if window_end.strip() else None
    games = parse_minutes(extra_games or "0", _("Zusätzliche Spielzeit"), MAX_EXTRA)
    check_credit(child, the_day, extra, override, games)
    return the_day, extra, override, games


def check_credit(child: Child, the_day: date, extra: int, override: time | None, games: int = 0) -> None:
    if the_day < today_local():
        raise ScheduleError(_("Gutschriften sind nur für heute oder künftige Tage möglich."))
    if not extra and override is None and not games:
        raise ScheduleError(_("Bitte Minuten, Spielzeit und/oder ein späteres Fensterende angeben."))
    from sqlalchemy.orm import object_session

    from .vacations import Calendar
    db = object_session(child)
    rule = Calendar.load(db).rule_for(child, the_day) if db is not None else rules_by_day(child).get(the_day.weekday())
    if games and (rule is None or rule.game_minutes is None):
        raise ScheduleError(_("Für diesen Tag ist kein Spiele-Limit eingestellt – Spielzeit-Gutschrift nicht nötig."))
    if override is not None and rule is not None:
        check_window(rule.window_start, override, _("Fensterende"))
    if override is not None and rule is not None and not end_after(rule.window_start, rule.window_end, override):
        raise ScheduleError(
            _("Das Fensterende muss nach dem regulären Ende ({time}) liegen.").format(time=f"{rule.window_end:%H:%M}"))


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
