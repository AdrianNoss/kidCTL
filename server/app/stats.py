"""Auswertung: Bildschirmzeit je Tag, Wochensummen, Programmzeit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from . import policy
from . import schedule as sch
from .models import AppUsage, Child, Device, DeviceApp, DeviceUser, UsageRecord

DAYS = 14


def apply_app_report(db: Session, dev: Device, msg: p.AppUsageReport) -> None:
    """Absolute Zählerstände übernehmen (Maximum, idempotent); nur zugeordnete User."""
    today = sch.today_local()
    mapping = dict(db.execute(select(DeviceUser.username, DeviceUser.child_id)
                              .where(DeviceUser.device_id == dev.id)).all())
    for e in msg.entries:
        if not (today - timedelta(days=7) <= e.day <= today + timedelta(days=1)):
            continue
        rec = db.scalar(select(AppUsage).where(AppUsage.device_id == dev.id, AppUsage.username == e.username,
                                               AppUsage.day == e.day, AppUsage.key == e.key))
        if rec is None:
            if mapping.get(e.username) is None:
                continue
            rec = AppUsage(device_id=dev.id, username=e.username, day=e.day, key=e.key,
                           child_id=mapping[e.username], seconds=0)
            db.add(rec)
        rec.seconds = max(rec.seconds, e.seconds)
        if e.key.startswith(GAME_PREFIX) and e.name:
            rec.name = _clean(e.name)


@dataclass
class DayBar:
    day: date
    used_minutes: int
    allowed_minutes: int

    @property
    def over(self) -> bool:
        return self.used_minutes > self.allowed_minutes


GAME_PREFIX = "game:"
LAUNCHERS = {"steam": "Steam", "heroic": "Heroic"}


def _clean(name: str) -> str:
    return "".join(ch for ch in name if ch.isprintable()).strip()[:255]


@dataclass
class AppRow:
    key: str
    name: str
    minutes: int
    launcher: str = ""  # bei Spielen: „Steam“ / „Heroic“


def daily(db: Session, child: Child, days: int = DAYS) -> list[DayBar]:
    today = sch.today_local()
    start = today - timedelta(days=days - 1)
    used = dict(db.execute(select(UsageRecord.day, func.sum(UsageRecord.seconds))
                           .where(UsageRecord.child_id == child.id, UsageRecord.day >= start)
                           .group_by(UsageRecord.day)).all())
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        out.append(DayBar(d, int(used.get(d, 0)) // 60, policy.day_status(db, child, d).allowed_minutes))
    return out


def week_sums(bars: list[DayBar]) -> dict[str, int]:
    today = sch.today_local()
    monday = today - timedelta(days=today.weekday())
    this = sum(b.used_minutes for b in bars if b.day >= monday)
    last = sum(b.used_minutes for b in bars if monday - timedelta(days=7) <= b.day < monday)
    return {"this_week": this, "last_week": last, "avg": round(sum(b.used_minutes for b in bars) / len(bars))}


def apps(db: Session, child: Child, days: int) -> list[AppRow]:
    start = sch.today_local() - timedelta(days=days - 1)
    rows = db.execute(select(AppUsage.key, func.sum(AppUsage.seconds))
                      .where(AppUsage.child_id == child.id, AppUsage.day >= start)
                      .group_by(AppUsage.key)).all()
    names = dict(db.execute(select(DeviceApp.key, DeviceApp.name)).all())
    names |= {k: n for k, n in db.execute(
        select(AppUsage.key, func.max(AppUsage.name)).where(
            AppUsage.child_id == child.id, AppUsage.key.startswith(GAME_PREFIX), AppUsage.name != "")
        .group_by(AppUsage.key)).all()}
    out = []
    for k, s in rows:
        if k.startswith(GAME_PREFIX):
            launcher = LAUNCHERS.get(k.split(":")[1], "")
            out.append(AppRow(k, names.get(k, k.rsplit(":", 1)[-1]), int(s) // 60, launcher))
        else:
            out.append(AppRow(k, names.get(k, k.split(":", 1)[-1].rsplit("/", 1)[-1]), int(s) // 60))
    return sorted((r for r in out if r.minutes > 0), key=lambda r: -r.minutes)
