"""„Jetzt sperren“: Kind sofort sperren (bis zur Freigabe oder für eine Dauer)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .models import Child, utcnow

# Formularwert -> Anzeige
DURATIONS = {
    "": "bis zur Freigabe",
    "30": "30 Minuten",
    "60": "1 Stunde",
    "120": "2 Stunden",
    "tomorrow": "bis morgen (00:00 Uhr)",
}


def until_for(choice: str, now: datetime | None = None) -> datetime | None:
    """Ende der Sperre (naiv, UTC wie alle Zeitstempel der DB); None = bis zur Freigabe."""
    if choice not in DURATIONS:
        raise ValueError("Ungültige Dauer")
    now = now or utcnow()
    if choice == "":
        return None
    if choice == "tomorrow":
        tz = ZoneInfo(settings.timezone)
        local = now.replace(tzinfo=timezone.utc).astimezone(tz)
        midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tz)
        return midnight.astimezone(timezone.utc).replace(tzinfo=None)
    return now + timedelta(minutes=int(choice))


def is_locked(child: Child, now: datetime | None = None) -> bool:
    if not child.locked:
        return False
    return child.locked_until is None or (now or utcnow()) < child.locked_until


def lock(child: Child, choice: str) -> None:
    child.locked, child.locked_until = True, until_for(choice)


def unlock(child: Child) -> None:
    child.locked, child.locked_until = False, None
