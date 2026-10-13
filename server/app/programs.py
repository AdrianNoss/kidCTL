"""Programm-Whitelist: App-Katalog der Geräte, Freigaben je Kind, Sperrprotokoll."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from .chat import device_ids_for_child
from .models import BlockedEvent, ChildProgram, Device, DeviceApp, DeviceUser

MODES = {"off": "Aus", "audit": "Nur protokollieren", "enforce": "Sperren"}
MAX_EVENTS_PER_REPORT = 500
LOG_LIMIT = 50


def apply_app_report(db: Session, dev: Device, msg: p.ReportApps) -> None:
    """Katalog des Geräts vollständig ersetzen (der Agent meldet immer alles)."""
    db.execute(delete(DeviceApp).where(DeviceApp.device_id == dev.id))
    seen = set()
    for a in msg.apps:
        if a.key in seen:
            continue
        seen.add(a.key)
        db.add(DeviceApp(device_id=dev.id, key=a.key, name=a.name, icon=a.icon, desktop_id=a.desktop_id))


def apply_blocked_report(db: Session, dev: Device, msg: p.BlockedReport) -> int:
    mapping = dict(db.execute(select(DeviceUser.username, DeviceUser.child_id)
                              .where(DeviceUser.device_id == dev.id)).all())
    n = 0
    for e in msg.events[:MAX_EVENTS_PER_REPORT]:
        child_id = mapping.get(e.username)
        if child_id is None:
            continue  # nicht zugeordnete User werden nicht protokolliert
        db.add(BlockedEvent(child_id=child_id, device_id=dev.id, username=e.username, at=_utc_naive(e.at),
                            kind=e.kind, key=e.key, name=e.name, detail=e.detail, audit=e.audit))
        n += 1
    return n


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@dataclass
class CatalogEntry:
    key: str
    name: str
    devices: int        # auf wie vielen Geräten des Kindes installiert
    allowed: bool
    missing: bool = False  # freigegeben, aber auf keinem Gerät (mehr) gemeldet


def catalog_for_child(db: Session, child_id: int) -> list[CatalogEntry]:
    devices = device_ids_for_child(db, child_id)
    allowed = set(allowed_keys(db, child_id))
    entries: dict[str, CatalogEntry] = {}
    if devices:
        for app in db.scalars(select(DeviceApp).where(DeviceApp.device_id.in_(devices))):
            e = entries.get(app.key)
            if e is None:
                entries[app.key] = CatalogEntry(app.key, app.name, 1, app.key in allowed)
            else:
                e.devices += 1
    for key in allowed - entries.keys():
        entries[key] = CatalogEntry(key, key.split(":", 1)[-1].rsplit("/", 1)[-1], 0, True, missing=True)
    return sorted(entries.values(), key=lambda e: (not e.allowed, e.name.lower()))


def allowed_keys(db: Session, child_id: int) -> list[str]:
    return sorted(db.scalars(select(ChildProgram.key).where(ChildProgram.child_id == child_id)))


def set_allowed(db: Session, child_id: int, keys: set[str]) -> None:
    current = set(allowed_keys(db, child_id))
    for key in current - keys:
        db.execute(delete(ChildProgram).where(ChildProgram.child_id == child_id, ChildProgram.key == key))
    for key in keys - current:
        db.add(ChildProgram(child_id=child_id, key=key))


def blocked_log(db: Session, child_id: int, limit: int = LOG_LIMIT) -> list[BlockedEvent]:
    return list(db.scalars(select(BlockedEvent).where(BlockedEvent.child_id == child_id)
                           .order_by(BlockedEvent.at.desc(), BlockedEvent.id.desc()).limit(limit)))
