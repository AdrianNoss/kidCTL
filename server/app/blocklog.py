"""Protokoll: gesperrte Aktionen und Spielsitzungen (Reiter „Spiele“). Filter (Art, Zeitraum, Status,
Benutzer, Suche), Tagesgruppen, Häufigste bzw. Meistgespielt. Einträge werden nach ``RETENTION_DAYS``
gelöscht (``prune``)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .models import BlockedEvent, GameSession

KINDS = {"": "Alle", "program": "Programme", "domain": "Websites", "game": "Spiele"}
GAME = "game"
LAUNCHERS = {"steam": "Steam", "heroic": "Heroic"}
RUNNING_SECONDS = 150  # Sitzung ohne neuere Meldung gilt danach als beendet
RANGES = {"1": "Heute", "7": "7 Tage", "30": "30 Tage", "all": "Alles (90 Tage)"}
STATUSES = {"": "Alle", "blocked": "Gesperrt", "audit": "Nur protokolliert"}
ORDERS = {"new": "Neueste zuerst", "old": "Älteste zuerst"}
PAGE = 200
MAX_LIMIT = 5000
TOP = 10
RETENTION_DAYS = 90
PRUNE_INTERVAL = 6 * 3600  # Sekunden
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


@dataclass(frozen=True)
class LogFilter:
    kind: str = ""
    range: str = "7"
    status: str = ""
    user: str = ""
    q: str = ""
    order: str = "new"
    limit: int = PAGE

    def url(self, base: str, **changes) -> str:
        """Link mit geänderten Filtern (Standardwerte werden weggelassen)."""
        f = replace(self, **changes)
        default = LogFilter()
        params = {k: v for k, v in vars(f).items() if v != getattr(default, k)}
        return base + ("?" + urlencode(params) if params else "")

    @property
    def active(self) -> bool:
        return bool(self.kind or self.status or self.user or self.q)


def parse(params) -> LogFilter:
    """Query-Parameter -> Filter; Unbekanntes fällt auf den Standard zurück."""
    def pick(name: str, allowed) -> str:
        v = str(params.get(name, getattr(LogFilter, name)))
        return v if v in allowed else getattr(LogFilter, name)
    try:
        limit = min(max(int(params.get("limit", PAGE)), PAGE), MAX_LIMIT)
    except ValueError:
        limit = PAGE
    return LogFilter(kind=pick("kind", KINDS), range=pick("range", RANGES), status=pick("status", STATUSES),
                     user=str(params.get("user", ""))[:64], q=str(params.get("q", "")).strip()[:100],
                     order=pick("order", ORDERS), limit=limit)


def _since(f: LogFilter, today: date) -> datetime | None:
    """Beginn des Zeitraums (lokale Mitternacht) als UTC ohne Zeitzone (wie in der DB)."""
    if f.range == "all":
        return None
    tz = ZoneInfo(settings.timezone)
    start = datetime.combine(today - timedelta(days=int(f.range) - 1), time(), tz)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search(f: LogFilter, *cols):
    pat = f"%{_escape_like(f.q.lower())}%"
    return or_(*(func.lower(c).like(pat, escape="\\") for c in cols))


def _where(child_id: int, f: LogFilter, today: date) -> list:
    cond = [BlockedEvent.child_id == child_id]
    if f.kind:
        cond.append(BlockedEvent.kind == f.kind)
    if f.status:
        cond.append(BlockedEvent.audit.is_(f.status == "audit"))
    if f.user:
        cond.append(BlockedEvent.username == f.user)
    if (since := _since(f, today)) is not None:
        cond.append(BlockedEvent.at >= since)
    if f.q:
        cond.append(_search(f, BlockedEvent.name, BlockedEvent.key, BlockedEvent.detail))
    return cond


def events(db: Session, child_id: int, f: LogFilter, today: date) -> tuple[list[BlockedEvent], int]:
    """(Einträge bis ``f.limit``, Gesamtzahl der Treffer)."""
    cond = _where(child_id, f, today)
    total = db.scalar(select(func.count()).select_from(BlockedEvent).where(*cond)) or 0
    order = (BlockedEvent.at.desc(), BlockedEvent.id.desc()) if f.order == "new" else (
        BlockedEvent.at.asc(), BlockedEvent.id.asc())
    rows = list(db.scalars(select(BlockedEvent).where(*cond).order_by(*order).limit(f.limit)))
    return rows, total


@dataclass
class TopEntry:
    key: str
    name: str
    kind: str
    count: int
    last: datetime


def top(db: Session, child_id: int, f: LogFilter, today: date, n: int = TOP) -> list[TopEntry]:
    """Häufigste Einträge im gefilterten Zeitraum."""
    rows = db.execute(select(BlockedEvent.key, func.max(BlockedEvent.name), BlockedEvent.kind,
                             func.count(), func.max(BlockedEvent.at))
                      .where(*_where(child_id, f, today))
                      .group_by(BlockedEvent.key, BlockedEvent.kind)
                      .order_by(func.count().desc(), func.max(BlockedEvent.at).desc()).limit(n)).all()
    return [TopEntry(*r) for r in rows]


def prune(db: Session, now: datetime | None = None) -> int:
    """Einträge älter als ``RETENTION_DAYS`` löschen (alle Kinder, auch Spielsitzungen)."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    n = db.execute(delete(BlockedEvent).where(BlockedEvent.at < cutoff)).rowcount or 0
    n += db.execute(delete(GameSession).where(GameSession.last_seen_at < cutoff)).rowcount or 0
    db.commit()
    return n


# ---------- Spielsitzungen ----------

def _game_where(child_id: int, f: LogFilter, today: date) -> list:
    cond = [GameSession.child_id == child_id]
    if f.user:
        cond.append(GameSession.username == f.user)
    if (since := _since(f, today)) is not None:
        cond.append(GameSession.last_seen_at >= since)   # auch über Mitternacht laufende Sitzungen
    if f.q:
        cond.append(_search(f, GameSession.name, GameSession.key))
    return cond


def sessions(db: Session, child_id: int, f: LogFilter, today: date) -> tuple[list[GameSession], int]:
    cond = _game_where(child_id, f, today)
    total = db.scalar(select(func.count()).select_from(GameSession).where(*cond)) or 0
    order = (GameSession.started_at.desc(), GameSession.id.desc()) if f.order == "new" else (
        GameSession.started_at.asc(), GameSession.id.asc())
    return list(db.scalars(select(GameSession).where(*cond).order_by(*order).limit(f.limit))), total


def session_minutes(s: GameSession) -> int:
    """Dauer in Minuten (Meldungen etwa minütlich: eine Meldung = 1 Minute)."""
    return int((s.last_seen_at - s.started_at).total_seconds() // 60) + 1


def is_running(s: GameSession, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - s.last_seen_at).total_seconds() <= RUNNING_SECONDS


def launcher(key: str) -> str:
    parts = key.split(":")
    return LAUNCHERS.get(parts[1], "") if len(parts) > 2 else ""


@dataclass
class GameTop:
    key: str
    name: str
    launcher: str
    minutes: int
    sessions: int
    last: datetime


def top_games(db: Session, child_id: int, f: LogFilter, today: date, n: int = TOP) -> list[GameTop]:
    """Meistgespielt im gefilterten Zeitraum (Summe der Sitzungsdauern)."""
    totals: dict[str, GameTop] = {}
    for s in db.scalars(select(GameSession).where(*_game_where(child_id, f, today))):
        t = totals.setdefault(s.key, GameTop(s.key, s.name, launcher(s.key), 0, 0, s.last_seen_at))
        t.minutes += session_minutes(s)
        t.sessions += 1
        if s.last_seen_at >= t.last:
            t.last, t.name = s.last_seen_at, s.name
    return sorted(totals.values(), key=lambda t: (-t.minutes, t.name.lower()))[:n]


def users(db: Session, child_id: int) -> list[str]:
    blocked = db.scalars(select(BlockedEvent.username).where(BlockedEvent.child_id == child_id).distinct())
    played = db.scalars(select(GameSession.username).where(GameSession.child_id == child_id).distinct())
    return sorted(set(blocked) | set(played))


def by_day(rows: list, today: date, attr: str = "at") -> list[tuple[str, list]]:
    """Nach lokalem Tag gruppiert (Reihenfolge der Einträge bleibt erhalten)."""
    tz = ZoneInfo(settings.timezone)
    out: list[tuple[str, list]] = []
    last = None
    for e in rows:
        d = getattr(e, attr).replace(tzinfo=timezone.utc).astimezone(tz).date()
        if d != last:
            label = ("Heute" if d == today else "Gestern" if d == today - timedelta(days=1)
                     else WEEKDAYS[d.weekday()]) + d.strftime(", %d.%m.%Y")
            out.append((label, []))
            last = d
        out[-1][1].append(e)
    return out
