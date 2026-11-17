"""Aufbewahrungsfristen (Etappe 2 Multi-Tenant, spec.md „Datenschutz, Backup, Aufbewahrung“).

Feste Fristen für alle Familien, je Datenart per Umgebung änderbar (``KIDCTL_KEEP_<NAME>_DAYS``). Aufgeräumt wird
im Plattformkontext über alle Familien – nur nach Zeitstempeln, ohne verschlüsselte Inhalte zu lesen.
Gelöschte Familien: sofort (Schlüssel verworfen, ``tenant.delete_family``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import (
    AppUsage, BlockedEvent, GameSession, LoginAttempt, Message, PairingCode, ParentSession, ParentToken, UsageRecord,
)

log = logging.getLogger("kidctl.retention")
PRUNE_INTERVAL = 6 * 3600


@dataclass(frozen=True)
class Rule:
    name: str          # für KIDCTL_KEEP_<NAME>_DAYS und das Log
    model: type
    column: str        # Zeitstempel bzw. Tag
    days: int
    label: str         # für die Doku


RULES = [
    Rule("LOG", BlockedEvent, "at", 90, "Protokoll gesperrter Aktionen"),
    Rule("GAMES", GameSession, "last_seen_at", 90, "Spielsitzungen"),
    Rule("APPS", AppUsage, "day", 90, "Programmnutzung je Programm"),
    Rule("USAGE", UsageRecord, "day", 395, "Bildschirmzeit je Tag (13 Monate)"),
    Rule("CHAT", Message, "created_at", 365, "Chat (12 Monate)"),
    Rule("LOGINS", LoginAttempt, "at", 30, "Anmeldeversuche"),
    Rule("SESSIONS", ParentSession, "expires_at", 30, "abgelaufene Sitzungen (nach Ablauf)"),
    Rule("TOKENS", ParentToken, "expires_at", 30, "abgelaufene Links (nach Ablauf)"),
    Rule("PAIRING", PairingCode, "expires_at", 1, "abgelaufene Kopplungscodes (nach Ablauf)"),
]


def days(rule: Rule) -> int:
    raw = os.environ.get(f"KIDCTL_KEEP_{rule.name}_DAYS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else rule.days


def prune_all(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Alles älter als die jeweilige Frist löschen. Rückgabe: gelöschte Zeilen je Datenart."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    out = {}
    for rule in RULES:
        col = getattr(rule.model, rule.column)
        cutoff = now - timedelta(days=days(rule))
        if col.type.python_type is date:
            cutoff = cutoff.date()
        n = db.execute(delete(rule.model).where(col < cutoff)).rowcount or 0
        if n:
            out[rule.name] = n
    db.commit()
    if out:
        log.info("Aufbewahrung: gelöscht %s", ", ".join(f"{k}={v}" for k, v in out.items()))
    return out
