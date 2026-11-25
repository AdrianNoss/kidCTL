"""Zeitkonto: nicht genutzte Minuten teilweise auf Folgetage übertragen (spec.md „Zeitkonto“).

Zustandslos aus der gemeldeten Nutzung berechnet (keine Buchungen), damit nachträgliche Meldungen einfach neu
gerechnet werden:

- Einzahlung eines vergangenen Tages d (ab ``child.bank_since``) = abgerundet ``Anteil × (Regel-Kontingent(d) −
  Nutzung(d))``. Gutschriften der Eltern und genutztes Guthaben zählen nicht mit (nichts schaukelt sich auf). Der
  Kontostand ist auf ``BALANCE_FACTOR × Deckel`` begrenzt.
- Eine Einzahlung von Tag d ist an den Tagen d+1 … d+Verfall nutzbar; das älteste Guthaben wird zuerst verbraucht.
- Nutzbar an einem Tag ist ``min(Deckel, Kontostand)``. Verbraucht wird, was die Nutzung über Regel-Kontingent und
  Gutschrift hinausgeht.
- Das Guthaben zählt nur für die Gesamtzeit: Spiele-Limit, Zeitfenster und Sperrzeiten bleiben unverändert.
- Die Policy enthält das heute nutzbare Guthaben als zusätzliche Tagesausnahme – der Agent braucht nichts Neues und
  setzt es auch offline bis Tagesende durch.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import schedule as sch
from . import vacations as vac
from .i18n import _
from .models import Child, DayException, UsageRecord

LOOKBACK_DAYS = 28          # so weit reicht die Rechnung zurück (deutlich mehr als der längste Verfall)
PERCENTS = (25, 50, 100)
CAP_MIN, CAP_MAX = 5, 120
EXPIRY_MIN, EXPIRY_MAX = 1, 14
BALANCE_FACTOR = 3          # Kontostand höchstens 3 × Deckel


@dataclass(frozen=True)
class BankDay:
    available: int          # an diesem Tag nutzbar (Minuten)
    balance: int            # Kontostand zu Tagesbeginn nach Verfall (Minuten)


def simulate(db: Session, child: Child, first: date, last: date, cal: vac.Calendar | None = None) -> dict[date, BankDay]:
    """Zeitkonto je Tag ``first`` … ``min(last, heute)``. Tage ohne Eintrag: kein Guthaben."""
    if not child.bank_enabled or child.bank_since is None:
        return {}
    today = sch.today_local()
    start, end = max(child.bank_since, first - timedelta(days=LOOKBACK_DAYS)), min(last, today)
    if start > end:
        return {}
    cal = cal or vac.Calendar.load(db)
    used = dict(db.execute(select(UsageRecord.day, func.sum(UsageRecord.seconds)).where(
        UsageRecord.child_id == child.id, UsageRecord.day >= start, UsageRecord.day <= end)
        .group_by(UsageRecord.day)).all())
    credits = dict(db.execute(select(DayException.day, func.sum(DayException.extra_minutes)).where(
        DayException.child_id == child.id, DayException.day >= start, DayException.day <= end)
        .group_by(DayException.day)).all())
    cap, expiry, percent = child.bank_cap_minutes, child.bank_expiry_days, child.bank_percent
    deposits: deque[list] = deque()     # [Tag der Einzahlung, Minuten], älteste zuerst
    out: dict[date, BankDay] = {}
    d = start
    while d <= end:
        while deposits and (d - deposits[0][0]).days > expiry:
            deposits.popleft()
        balance = sum(m for _d, m in deposits)
        available = min(cap, balance)
        if d >= first:
            out[d] = BankDay(available, balance)
        if d >= today:
            break       # heute ist noch nicht abgeschlossen
        rule = cal.rule_for(child, d)
        quota = rule.quota_minutes if rule else 0
        minutes = int(used.get(d, 0)) // 60
        spend = min(available, max(0, minutes - quota - int(credits.get(d, 0) or 0)))
        while spend:
            take = min(spend, deposits[0][1])
            deposits[0][1] -= take
            spend -= take
            if not deposits[0][1]:
                deposits.popleft()
        room = BALANCE_FACTOR * cap - sum(m for _d, m in deposits)
        deposit = min(max(0, quota - minutes) * percent // 100, max(0, room))
        if deposit:
            deposits.append([d, deposit])
        d += timedelta(days=1)
    return out


def for_day(db: Session, child: Child, day: date, cal: vac.Calendar | None = None) -> BankDay:
    return simulate(db, child, day, day, cal).get(day, BankDay(0, 0))


# ---------- Einstellungen ----------

def apply_settings(child: Child, form, today: date) -> None:
    """Formular „Zeitkonto“ übernehmen (kein Commit). Einschalten zählt ab dem Folgetag, Ausschalten leert das Konto."""
    enabled = form.get("bank_enabled") == "1"
    try:
        percent = int(str(form.get("bank_percent", "")))
        cap = int(str(form.get("bank_cap_minutes", "")).strip())
        expiry = int(str(form.get("bank_expiry_days", "")).strip())
    except ValueError:
        raise sch.ScheduleError(_("Zeitkonto: ungültige Eingabe.")) from None
    if percent not in PERCENTS:
        raise sch.ScheduleError(_("Zeitkonto: ungültiger Anteil."))
    if not CAP_MIN <= cap <= CAP_MAX:
        raise sch.ScheduleError(_("Zeitkonto: höchstens pro Tag muss zwischen {min} und {max} Minuten liegen.")
                                .format(min=CAP_MIN, max=CAP_MAX))
    if not EXPIRY_MIN <= expiry <= EXPIRY_MAX:
        raise sch.ScheduleError(_("Zeitkonto: Verfall muss zwischen {min} und {max} Tagen liegen.")
                                .format(min=EXPIRY_MIN, max=EXPIRY_MAX))
    if enabled and not child.bank_enabled:
        child.bank_since = today + timedelta(days=1)
    elif not enabled:
        child.bank_since = None
    child.bank_enabled, child.bank_percent, child.bank_cap_minutes, child.bank_expiry_days = enabled, percent, cap, expiry
