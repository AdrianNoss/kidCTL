"""Hintergrund-Alarme: Gerät hat sich zu lange nicht gemeldet."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import push
from .db import SessionLocal
from .models import Device, utcnow
from .ws import manager

log = logging.getLogger("kidctl.alarms")

CHECK_INTERVAL = 300  # Sekunden


def due_offline_alarms(db: Session) -> list[tuple[Device, int]]:
    """Geräte, für die jetzt ein Offline-Alarm fällig ist; markiert sie als alarmiert.

    Ein Alarm pro Offline-Phase: erneut erst, nachdem sich das Gerät wieder gemeldet hat.
    """
    now = utcnow()
    due = []
    for dev in db.scalars(select(Device).where(Device.revoked_at.is_(None))):
        if manager.is_online(dev.id):
            continue
        seen = dev.last_seen_at or dev.created_at
        if now - seen < timedelta(hours=dev.alert_after_hours):
            continue
        if dev.offline_alerted_at is not None and dev.offline_alerted_at >= seen:
            continue
        dev.offline_alerted_at = now
        due.append((dev, int((now - seen).total_seconds() // 3600)))
    db.commit()
    return due


def check_once() -> int:
    with SessionLocal() as db:
        due = due_offline_alarms(db)
        for dev, hours in due:
            log.warning("Offline-Alarm: Gerät %s (%s) seit %d h ohne Meldung", dev.id, dev.name, hours)
            push.send_to_parents_sync(f"Gerät „{dev.name}“ offline",
                                      f"Seit {hours} Stunden keine Meldung.",
                                      url=f"/devices/{dev.id}", tag=f"offline-{dev.id}")
    return len(due)


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(check_once)
        except Exception:
            log.exception("Alarm-Prüfung fehlgeschlagen")
        await asyncio.sleep(CHECK_INTERVAL)
