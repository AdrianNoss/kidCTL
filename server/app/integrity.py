"""Integritätsmeldungen der Agenten speichern und bei neuen Fehlern die Eltern alarmieren."""

from __future__ import annotations

import json
import logging

from kidctl_common import protocol as p

from . import push
from .models import Device, utcnow

log = logging.getLogger("kidctl.integrity")


def apply_report(dev: Device, msg: p.IntegrityReport) -> list[p.IntegrityCheck]:
    """Speichert den Zustand; gibt die *neu* fehlschlagenden Prüfungen zurück (kein Commit)."""
    before = {c["id"] for c in dev.integrity_failed}
    dev.integrity_json = json.dumps([c.model_dump() for c in msg.checks], ensure_ascii=False)
    dev.integrity_at = utcnow()
    new = [c for c in msg.checks if not c.ok and c.id not in before]
    for c in new:
        log.warning("Integritätsfehler auf Gerät %s (%s): %s – %s", dev.id, dev.name, c.label, c.detail)
    return new


def alarm(dev: Device, new: list[p.IntegrityCheck]) -> None:
    if not new:
        return
    labels = ", ".join(c.label for c in new)
    push.notify_parents(f"Integritätsfehler auf „{dev.name}“", labels[:250],
                        url=f"/devices/{dev.id}", tag=f"integrity-{dev.id}")
