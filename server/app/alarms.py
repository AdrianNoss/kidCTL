"""Hintergrund-Alarme: Gerät hat sich zu lange nicht gemeldet; Agent einen Tag nach einer neuen Version im
Repository immer noch veraltet (einmal je Gerät und Version)."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import push, versions
from .i18n import _
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


UPDATE_GRACE = timedelta(days=1)   # so lange nach Veröffentlichung Zeit für das Systemupdate


def due_update_alarms(db: Session, release: versions.Release | None = None) -> list[tuple[Device, str]]:
    """Geräte mit veraltetem Agenten, für die jetzt ein Hinweis fällig ist; markiert sie (kein Commit)."""
    release = release or versions.latest()
    if release is None or utcnow() - release.published < UPDATE_GRACE:
        return []
    due = []
    for dev in db.scalars(select(Device).where(Device.revoked_at.is_(None))):
        if versions.update_for(dev.agent_version, release) and dev.update_alerted != release.version:
            dev.update_alerted = release.version
            due.append((dev, release.version))
    return due


def check_once() -> int:
    with SessionLocal() as db:
        for dev, version in due_update_alarms(db):
            log.warning("Agent auf Gerät %s (%s) veraltet: %s, verfügbar %s", dev.id, dev.name, dev.agent_version,
                        version)
            push.send_to_parents_sync(
                lambda name=dev.name: _("Update für „{name}“ verfügbar").format(name=name),
                lambda old=dev.agent_version, new=version: _(
                    "Agent {old} ist veraltet, verfügbar ist {new}. Auf dem Rechner das Systemupdate ausführen (pacman -Syu bzw. apt upgrade).").format(
                    old=old, new=new),
                url=f"/devices/{dev.id}", tag=f"update-{dev.id}")
        db.commit()
        due = due_offline_alarms(db)
        for dev, hours in due:
            log.warning("Offline-Alarm: Gerät %s (%s) seit %d h ohne Meldung", dev.id, dev.name, hours)
            push.send_to_parents_sync(lambda name=dev.name: _("Gerät „{name}“ offline").format(name=name),
                                      lambda h=hours: _("Seit {hours} Stunden keine Meldung.").format(hours=h),
                                      url=f"/devices/{dev.id}", tag=f"offline-{dev.id}")
    return len(due)


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(check_once)
        except Exception:
            log.exception("Alarm-Prüfung fehlgeschlagen")
        await asyncio.sleep(CHECK_INTERVAL)
