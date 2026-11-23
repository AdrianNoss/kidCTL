"""Ferien und gesetzliche Feiertage: welche Regel an einem Datum gilt.

- Ferienzeiträume gehören der Familie, die Ferienvorlage dem Kind (leer = Wochenvorlage gilt weiter).
- Feiertage je Land/Bundesland/Kanton rechnet die Bibliothek ``holidays`` lokal aus; ein Feiertag gilt wie der in
  ``HolidayRegion.as_weekday`` gewählte Wochentag der an dem Datum geltenden Vorlage.
- Der Agent kennt nur Wochentagsregeln. Die Policy enthält deshalb die geltenden Regeln von heute … heute+6
  (``week_rules``); ``run_forever`` sendet nach dem Tageswechsel geänderte Policies an verbundene Geräte.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays as holidays_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import i18n
from . import schedule as sch
from .config import settings
from .i18n import N_, _
from .models import Child, HolidayRegion, Vacation

log = logging.getLogger("kidctl.vacations")

COUNTRIES = {"DE": N_("Deutschland"), "AT": N_("Österreich"), "CH": N_("Schweiz")}
MAX_DAYS = 366          # längster Ferienzeitraum
UPCOMING_DAYS = 14      # Sondertage, die der Tab „Zeiten“ zeigt
NAME_MAX = 64


class VacationError(ValueError):
    pass


# ---------- Feiertage ----------

@lru_cache(maxsize=None)
def subdivisions(country: str) -> tuple[tuple[str, str], ...]:
    """(Code, Name) der Bundesländer/Kantone laut ``holidays`` (Name = erster Alias, sonst der Code)."""
    cls = holidays_lib.country_holidays(country).__class__
    names: dict[str, str] = {}
    for alias, code in getattr(cls, "subdivisions_aliases", {}).items():
        names.setdefault(code, alias)
    return tuple(sorted(((c, names.get(c, c)) for c in cls.subdivisions), key=lambda x: x[1]))


@lru_cache(maxsize=256)
def holiday_dates(country: str, subdiv: str, year: int, lang: str = "de") -> dict[date, str]:
    """Gesetzliche Feiertage eines Jahres -> Name in ``lang`` (de/en)."""
    h = holidays_lib.country_holidays(country, subdiv=subdiv or None, years=year,
                                      language="en_US" if lang == "en" else "de")
    return dict(h.items())


def valid_region(country: str, subdiv: str) -> bool:
    return country in COUNTRIES and (subdiv == "" or subdiv in dict(subdivisions(country)))


# ---------- Kalender einer Familie ----------

@dataclass
class Calendar:
    """Ferienzeiträume und Feiertagseinstellung der Familie im Sitzungskontext (einmal laden, oft fragen)."""
    vacations: list[Vacation]
    region: HolidayRegion | None

    @classmethod
    def load(cls, db: Session) -> Calendar:
        return cls(list(db.scalars(select(Vacation).order_by(Vacation.first_day, Vacation.id))),
                   db.scalar(select(HolidayRegion)))

    def vacation(self, day: date) -> Vacation | None:
        return next((v for v in self.vacations if v.first_day <= day <= v.last_day), None)

    def holiday(self, day: date) -> str | None:
        """Name des gesetzlichen Feiertags (Sprache der laufenden Anfrage) oder ``None``."""
        if self.region is None:
            return None
        return holiday_dates(self.region.country, self.region.subdiv, day.year, i18n.current()).get(day)

    def effective_weekday(self, day: date) -> int:
        return self.region.as_weekday if self.holiday(day) is not None else day.weekday()

    def rule_for(self, child: Child, day: date):
        """Geltende Regel (``WeekdayRule`` oder ``VacationRule``) für ``day`` oder ``None``."""
        weekday = self.effective_weekday(day)
        if self.vacation(day) is not None:
            rule = next((r for r in child.vacation_rules if r.weekday == weekday), None)
            if rule is not None:
                return rule
        return next((r for r in child.rules if r.weekday == weekday), None)

    def week_rules(self, child: Child, today: date) -> list[tuple[int, object]]:
        """(Wochentag, geltende Regel) für heute … heute+6, nach Wochentag sortiert – so versteht es der Agent."""
        days = (today + timedelta(days=i) for i in range(7))
        out = [(d.weekday(), self.rule_for(child, d)) for d in days]
        return sorted(((wd, r) for wd, r in out if r is not None), key=lambda x: x[0])

    def special_days(self, start: date, days: int = UPCOMING_DAYS) -> list[tuple[date, date, str | None, str | None]]:
        """(von, bis, Ferienname, Feiertagsname) ab ``start``; aufeinanderfolgende gleiche Tage zusammengefasst.
        Ferienname ``""`` = Ferien ohne Namen, ``None`` = keine Ferien."""
        out: list[list] = []
        for i in range(days):
            d = start + timedelta(days=i)
            v, h = self.vacation(d), self.holiday(d)
            if v is None and h is None:
                continue
            key = (v.name if v is not None else None, h)
            if out and out[-1][1] == d - timedelta(days=1) and tuple(out[-1][2:]) == key:
                out[-1][1] = d
            else:
                out.append([d, d, *key])
        return [tuple(x) for x in out]


def day_label(cal: Calendar, day: date) -> str | None:
    """Kurztext für den Tab „Heute“: „Ferien: Sommerferien“, „Feiertag: Ostermontag“ (oder beides)."""
    parts = []
    v = cal.vacation(day)
    if v is not None:
        parts.append(_("Ferien: {name}").format(name=v.name) if v.name else _("Ferien"))
    h = cal.holiday(day)
    if h is not None:
        parts.append(_("Feiertag: {name}").format(name=h))
    return " · ".join(parts) or None


# ---------- Eingaben ----------

def parse_vacation(name: str, first: str, last: str) -> tuple[str, date, date]:
    try:
        first_day, last_day = date.fromisoformat(first.strip()), date.fromisoformat(last.strip())
    except ValueError:
        raise VacationError(_("Ungültiges Datum.")) from None
    if last_day < first_day:
        raise VacationError(_("Der letzte Ferientag darf nicht vor dem ersten liegen."))
    if (last_day - first_day).days + 1 > MAX_DAYS:
        raise VacationError(_("Ein Ferienzeitraum darf höchstens {days} Tage lang sein.").format(days=MAX_DAYS))
    if last_day < sch.today_local():
        raise VacationError(_("Der Ferienzeitraum liegt in der Vergangenheit."))
    return name.strip()[:NAME_MAX], first_day, last_day


def set_region(db: Session, country: str, subdiv: str, as_weekday: str) -> None:
    """Feiertagseinstellung übernehmen (``country`` leer = keine Feiertage). Kein Commit."""
    region = db.scalar(select(HolidayRegion))
    if not country:
        if region is not None:
            db.delete(region)
        return
    if not valid_region(country, subdiv):
        raise VacationError(_("Unbekanntes Land oder Bundesland."))
    if not as_weekday.isdigit() or not 0 <= int(as_weekday) <= 6:
        raise VacationError(_("Ungültiger Wochentag."))
    if region is None:
        region = HolidayRegion(country=country)
        db.add(region)
    region.country, region.subdiv, region.as_weekday = country, subdiv, int(as_weekday)


# ---------- Tageswechsel: Policies nachschieben ----------

def seconds_to_midnight(now: datetime | None = None) -> float:
    tz = ZoneInfo(settings.timezone)
    now = now or datetime.now(tz)
    nxt = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tz)
    # In UTC rechnen: Differenzen in derselben Zone ignorieren die Zeitumstellung
    return max(0.0, (nxt.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds())


async def refresh_online() -> int:
    """Allen verbundenen Geräten mit veralteter Policy die aktuelle senden; Rückgabe: Anzahl gesendet."""
    from . import policy
    from .models import Device
    from .tenant import family_ids, family_session, platform_session
    from .ws import manager
    with platform_session() as db:
        fams = family_ids(db)
    sent = 0
    for fid in fams:
        with family_session(fid) as db:
            for dev in db.scalars(select(Device).where(Device.revoked_at.is_(None))):
                if manager.is_online(dev.id) and policy.build_policy(db, dev.id).version != dev.policy_version:
                    await policy.send_policy(db, dev.id)
                    sent += 1
    return sent


async def run_forever() -> None:
    """Kurz nach jedem Tageswechsel (Heimatzeit) die rollierende Wochenregel an verbundene Geräte senden."""
    last = sch.today_local()
    while True:
        await asyncio.sleep(min(seconds_to_midnight() + 5, 3600))
        day = sch.today_local()
        if day == last:
            continue
        last = day
        try:
            n = await refresh_online()
            if n:
                log.info("Tageswechsel: %d Policies neu gesendet", n)
        except Exception:
            log.exception("Policy-Aktualisierung zum Tageswechsel fehlgeschlagen")
