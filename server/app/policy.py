"""Policy-Berechnung/-Verteilung an Agenten und geräteübergreifende Nutzungszeit."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from . import lock as lk
from . import schedule as sch
from .chat import device_ids_for_child
from .programs import allowed_keys
from .webfilter import web_policy
from .config import settings
from .db import SessionLocal
from .models import Child, ChildBlocklist, DayException, Device, DeviceUser, UsageRecord, utcnow
from .ws import manager

log = logging.getLogger("kidctl.policy")

# Meldungen außerhalb dieses Bereichs (relativ zu heute) werden ignoriert (falsche Uhr o. Ä.)
USAGE_DAYS_BACK = 7


# ---------- Policy ----------

def build_policy(db: Session, device_id: int) -> p.Policy:
    mapped = db.execute(select(DeviceUser.username, DeviceUser.child_id).where(
        DeviceUser.device_id == device_id, DeviceUser.child_id.is_not(None))
        .order_by(DeviceUser.username)).all()
    # Ungültige Namen (z. B. von älteren Agenten gemeldet) nie verteilen – das Protokoll lehnt sie ab
    users = {u: c for u, c in mapped if re.fullmatch(p.USERNAME_PATTERN, u)}
    since = sch.today_local() - timedelta(days=1)  # Puffer für Uhr-/Zeitzonenränder
    children = []
    for child in db.scalars(select(Child).where(Child.id.in_(set(users.values()))).order_by(Child.id)):
        excs = db.scalars(select(DayException).where(
            DayException.child_id == child.id, DayException.day >= since).order_by(DayException.day))
        children.append(p.ChildPolicy(
            id=child.id, name=child.name,
            rules=[p.DayRule(weekday=r.weekday, quota_minutes=r.quota_minutes,
                             window_start=r.window_start, window_end=r.window_end) for r in child.rules],
            exceptions=[p.DayOverride(day=e.day, extra_minutes=e.extra_minutes,
                                      window_end_override=e.window_end_override) for e in excs],
            program_mode=child.program_mode,
            allowed_programs=allowed_keys(db, child.id),
            web=web_policy(db, child),
            locked=lk.is_locked(child),
            locked_until=child.locked_until.replace(tzinfo=timezone.utc) if lk.is_locked(child) and child.locked_until else None,
        ))
    body = p.Policy(version="", timezone=settings.timezone, users=users, children=children)
    digest = hashlib.sha256(body.model_dump_json(exclude={"version"}).encode()).hexdigest()[:16]
    return body.model_copy(update={"version": digest})


async def send_policy(db: Session, device_id: int) -> None:
    """Sendet Policy + aktuellen Nutzungsstand, falls das Gerät online ist."""
    if not manager.is_online(device_id):
        return
    pol = build_policy(db, device_id)
    try:
        await manager.send(device_id, pol.model_dump(mode="json"))
        today = sch.today_local()
        for child in pol.children:
            await manager.send(device_id, p.UsageUpdate(
                child_id=child.id, day=today,
                others_seconds=others_seconds(db, device_id, child.id, today)).model_dump(mode="json"))
    except Exception:
        log.warning("Policy an Gerät %s nicht zustellbar", device_id, exc_info=True)


async def push_policies(db: Session, device_ids: set[int]) -> None:
    for device_id in sorted(device_ids):
        await send_policy(db, device_id)


def policy_current(db: Session, device: Device) -> bool:
    return bool(device.policy_version) and device.policy_version == build_policy(db, device.id).version


# ---------- Nutzungszeit ----------

def record_usage(db: Session, device_id: int, report: p.UsageReport) -> set[tuple[int, date]]:
    """Übernimmt absolute Zählerstände (Maximum). Gibt geänderte (Kind, Tag)-Paare zurück."""
    today = sch.today_local()
    mapping = dict(db.execute(select(DeviceUser.username, DeviceUser.child_id).where(
        DeviceUser.device_id == device_id)).all())
    changed: set[tuple[int, date]] = set()
    for e in report.entries:
        if not (today - timedelta(days=USAGE_DAYS_BACK) <= e.day <= today + timedelta(days=1)):
            continue
        rec = db.scalar(select(UsageRecord).where(
            UsageRecord.device_id == device_id, UsageRecord.username == e.username,
            UsageRecord.day == e.day))
        if rec is None:
            child_id = mapping.get(e.username)
            if child_id is None:
                continue  # nicht zugeordnete User werden nicht erfasst
            rec = UsageRecord(device_id=device_id, username=e.username, day=e.day,
                              child_id=child_id, seconds=0)
            db.add(rec)
        if e.seconds > rec.seconds:
            rec.seconds, rec.updated_at = e.seconds, utcnow()
            if rec.child_id is not None:
                changed.add((rec.child_id, e.day))
    db.commit()
    return changed


def others_seconds(db: Session, device_id: int, child_id: int, day: date) -> int:
    return db.scalar(select(func.coalesce(func.sum(UsageRecord.seconds), 0)).where(
        UsageRecord.child_id == child_id, UsageRecord.day == day,
        UsageRecord.device_id != device_id)) or 0


async def push_usage_updates(db: Session, changed: set[tuple[int, date]], source_device: int) -> None:
    """Informiert die *anderen* online-Geräte eines Kindes über neue Nutzungszeit."""
    for child_id, day in changed:
        for device_id in device_ids_for_child(db, child_id) - {source_device}:
            if manager.is_online(device_id):
                await manager.send(device_id, p.UsageUpdate(
                    child_id=child_id, day=day,
                    others_seconds=others_seconds(db, device_id, child_id, day)).model_dump(mode="json"))


@dataclass
class DayStatus:
    used_seconds: int
    allowed_minutes: int
    window_start: object
    window_end: object

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.allowed_minutes - self.used_seconds // 60)

    @property
    def overrun_minutes(self) -> int:
        """Überziehung (z. B. durch gleichzeitige Offline-Nutzung) – nur Anzeige."""
        return max(0, self.used_seconds // 60 - self.allowed_minutes)


def day_status(db: Session, child: Child, day: date | None = None) -> DayStatus:
    day = day or sch.today_local()
    rule = sch.rules_by_day(child).get(day.weekday())
    exc = db.scalar(select(DayException).where(DayException.child_id == child.id, DayException.day == day))
    used = db.scalar(select(func.coalesce(func.sum(UsageRecord.seconds), 0)).where(
        UsageRecord.child_id == child.id, UsageRecord.day == day)) or 0
    quota = (rule.quota_minutes if rule else 0) + (exc.extra_minutes if exc else 0)
    end = exc.window_end_override if exc and exc.window_end_override else (rule.window_end if rule else None)
    return DayStatus(used_seconds=used, allowed_minutes=quota,
                     window_start=rule.window_start if rule else None, window_end=end)


async def push_for_lists(list_ids: set[str]) -> None:
    """Neue Sperrlisten-Versionen: Policies der Geräte betroffener Kinder neu senden."""
    with SessionLocal() as db:
        children = set(db.scalars(select(ChildBlocklist.child_id).where(ChildBlocklist.list_id.in_(list_ids))))
        devices = set()
        for cid in children:
            devices |= device_ids_for_child(db, cid)
        await push_policies(db, devices)
