"""Nutzungsbericht per E-Mail (wöchentlich oder monatlich, je Elternteil aktivierbar), gegliedert nach Kindern.

- Wochenbericht: montags ab ``SEND_HOUR`` Uhr für die Vorwoche (Mo–So).
- Monatsbericht: am 1. ab ``SEND_HOUR`` Uhr für den Vormonat.
- ``Parent.report_sent`` hält den zuletzt gesendeten Zeitraum: kein Doppelversand nach Neustarts; war der
  Server zum Termin aus, wird der Bericht nachgeholt, solange der nächste Zeitraum noch nicht abgelaufen ist.
- Beim Aktivieren gilt der letzte abgeschlossene Zeitraum als gesendet (der erste Bericht kommt zum
  nächsten Termin; „Jetzt senden“ verschickt ihn sofort).
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import i18n, mail, policy, stats
from . import schedule as sch
from .i18n import N_, _
from .config import settings
from .db import SessionLocal
from .models import BlockedEvent, Child, DayException, GameSession, Message, Parent
from .web import templates

log = logging.getLogger("kidctl.report")

INTERVALS = {"": N_("Aus"), "weekly": N_("Wöchentlich (montags)"), "monthly": N_("Monatlich (am 1.)")}
SEND_HOUR = 7
CHECK_SECONDS = 600
RETRY_SECONDS = 3600          # nach fehlgeschlagenem Versand frühestens wieder
TOP = 5
BAR_HEIGHT = 72               # px, höchster Balken im Tagesdiagramm


@dataclass(frozen=True)
class Period:
    interval: str
    start: date
    end: date                 # einschließlich

    @property
    def key(self) -> str:
        if self.interval == "weekly":
            y, w, _wd = self.start.isocalendar()
            return f"{y}-W{w:02d}"
        return f"{self.start:%Y-%m}"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    # Texte in der aktuellen Sprache (beim Versand: Sprache des Empfängers)
    @property
    def title(self) -> str:
        return _("Wochenbericht") if self.interval == "weekly" else _("Monatsbericht")

    @property
    def label(self) -> str:
        if self.interval == "weekly":
            return f"{i18n.fmt_date(self.start, year=False)}–{i18n.fmt_date(self.end)}"
        return f"{i18n.month_name(self.start.month)} {self.start.year}"

    @property
    def compare_label(self) -> str:
        return _("Vorwoche") if self.interval == "weekly" else _("Vormonat")

    def previous(self) -> Period:
        if self.interval == "weekly":
            return Period("weekly", self.start - timedelta(days=7), self.start - timedelta(days=1))
        end = self.start - timedelta(days=1)
        return Period("monthly", end.replace(day=1), end)


def last_period(interval: str, today: date) -> Period:
    """Letzter abgeschlossener Zeitraum vor ``today``."""
    if interval == "weekly":
        monday = today - timedelta(days=today.weekday())
        return Period("weekly", monday - timedelta(days=7), monday - timedelta(days=1))
    end = today.replace(day=1) - timedelta(days=1)
    return Period("monthly", end.replace(day=1), end)


def due(parent: Parent, now_local: datetime) -> Period | None:
    """Fälliger Bericht für ``parent`` (oder ``None``)."""
    if parent.report_interval not in ("weekly", "monthly") or not parent.email:
        return None
    p = last_period(parent.report_interval, now_local.date())
    if parent.report_sent == p.key or (p.end == now_local.date() - timedelta(days=1) and now_local.hour < SEND_HOUR):
        return None   # schon gesendet bzw. Stichtag, aber noch vor der Versandzeit
    return p


def activate(parent: Parent, interval: str, today: date) -> None:
    parent.report_interval = interval
    parent.report_sent = last_period(interval, today).key if interval else ""


# ---------- Daten ----------

@dataclass
class Day:
    day: date
    used: int                 # Minuten
    allowed: int

    @property
    def over(self) -> bool:
        return self.used > self.allowed


@dataclass
class ChildReport:
    child: Child
    days: list[Day]
    total: int
    prev_total: int
    apps: list[stats.AppRow]
    games: list[stats.AppRow]
    game_sessions: int
    blocked: int
    blocked_top: list[tuple[str, str, int]] = field(default_factory=list)   # (Art, Name, Anzahl)
    credits: int = 0
    requests: int = 0
    approved: int = 0

    @property
    def avg(self) -> int:
        return round(self.total / len(self.days)) if self.days else 0

    @property
    def active_days(self) -> int:
        return sum(1 for d in self.days if d.used)

    @property
    def over_days(self) -> list[Day]:
        return [d for d in self.days if d.over]

    @property
    def change(self) -> int | None:
        """Veränderung gegenüber dem vorherigen Zeitraum in Prozent (``None`` ohne Vergleichswert)."""
        return round(100 * (self.total - self.prev_total) / self.prev_total) if self.prev_total else None


def _utc_bounds(p: Period) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.timezone)
    lo = datetime.combine(p.start, time(), tz).astimezone(timezone.utc).replace(tzinfo=None)
    hi = datetime.combine(p.end + timedelta(days=1), time(), tz).astimezone(timezone.utc).replace(tzinfo=None)
    return lo, hi


def child_report(db: Session, child: Child, p: Period) -> ChildReport:
    days = []
    for i in range(p.days):
        st = policy.day_status(db, child, p.start + timedelta(days=i))
        days.append(Day(p.start + timedelta(days=i), st.used_seconds // 60, st.allowed_minutes))
    prev = p.previous()
    prev_total = sum(policy.day_status(db, child, prev.start + timedelta(days=i)).used_seconds // 60
                     for i in range(prev.days))
    rows = stats.apps_between(db, child, p.start, p.end)
    lo, hi = _utc_bounds(p)
    blocked_where = (BlockedEvent.child_id == child.id, BlockedEvent.at >= lo, BlockedEvent.at < hi,
                     BlockedEvent.audit.is_(False))
    blocked_top = [(k, n, c) for k, n, c in db.execute(
        select(BlockedEvent.kind, func.max(BlockedEvent.name), func.count()).where(*blocked_where)
        .group_by(BlockedEvent.kind, BlockedEvent.key).order_by(func.count().desc()).limit(TOP)).all()]
    requests = list(db.scalars(select(Message.status).where(
        Message.child_id == child.id, Message.kind == "extension_request",
        Message.created_at >= lo, Message.created_at < hi)))
    return ChildReport(
        child=child, days=days, total=sum(d.used for d in days), prev_total=prev_total,
        apps=[r for r in rows if not r.launcher][:TOP], games=[r for r in rows if r.launcher][:TOP],
        game_sessions=db.scalar(select(func.count()).select_from(GameSession).where(
            GameSession.child_id == child.id, GameSession.started_at >= lo, GameSession.started_at < hi)) or 0,
        blocked=db.scalar(select(func.count()).select_from(BlockedEvent).where(*blocked_where)) or 0,
        blocked_top=blocked_top,
        credits=db.scalar(select(func.coalesce(func.sum(DayException.extra_minutes), 0)).where(
            DayException.child_id == child.id, DayException.day >= p.start, DayException.day <= p.end)) or 0,
        requests=len(requests), approved=sum(1 for s in requests if s == "approved"),
    )


def build(db: Session, p: Period) -> list[ChildReport]:
    return [child_report(db, c, p) for c in db.scalars(select(Child).order_by(Child.name))]


# ---------- Darstellung ----------

def _short(minutes: int) -> str:
    """Kurzform für Balkenbeschriftungen: „45m“, „1:20“."""
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}:{m:02d}"


def render(db: Session, parent: Parent, p: Period) -> tuple[str, str, str]:
    """(Betreff, Text, HTML) in der Sprache des Empfängers."""
    with i18n.use(i18n.parent_language(parent)):
        reports = build(db, p)
        subject = f"KidCTL: {p.title} {p.label}"
        ctx = dict(parent=parent, period=p, reports=reports, base_url=settings.base_url, fmt=sch.fmt_minutes,
                   short=_short, bar_height=BAR_HEIGHT, intervals=INTERVALS)
        html = templates.env.get_template("mail/report.html").render(**ctx)
        text = templates.env.get_template("mail/report.txt").render(**ctx)
    return subject, text, html


def send(db: Session, parent: Parent, p: Period) -> bool:
    subject, text, html = render(db, parent, p)
    return mail.send(parent.email, subject, text, html)


# ---------- Versand ----------

_failed: dict[int, float] = {}   # parent_id -> Zeitpunkt (monoton) des letzten Fehlschlags


def send_due(db: Session, now_local: datetime | None = None) -> int:
    """Fällige Berichte senden; Anzahl gesendeter Mails."""
    now_local = now_local or datetime.now(ZoneInfo(settings.timezone))
    sent = 0
    for parent in db.scalars(select(Parent).where(Parent.report_interval != "", Parent.email != "")):
        p = due(parent, now_local)
        if p is None or _time.monotonic() - _failed.get(parent.id, -RETRY_SECONDS) < RETRY_SECONDS:
            continue
        if send(db, parent, p):
            parent.report_sent = p.key
            db.commit()
            _failed.pop(parent.id, None)
            sent += 1
        else:
            _failed[parent.id] = _time.monotonic()
    return sent


async def run_forever() -> None:
    while True:
        if settings.mail_enabled:
            try:
                def run() -> int:
                    with SessionLocal() as db:
                        return send_due(db)
                if n := await asyncio.to_thread(run):
                    log.info("Nutzungsbericht: %d Mail(s) gesendet", n)
            except Exception:
                log.exception("Nutzungsbericht nicht versendbar")
        await asyncio.sleep(CHECK_SECONDS)
