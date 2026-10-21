"""„Jetzt sperren“: Kind sofort sperren (bis zur Freigabe oder für eine Dauer)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .i18n import N_, _
from .config import settings
from .models import Child, utcnow

# Formularwert -> Anzeige
DURATIONS = {
    "": N_("bis zur Freigabe"),
    "30": N_("30 Minuten"),
    "60": N_("1 Stunde"),
    "120": N_("2 Stunden"),
    "tomorrow": N_("bis morgen (00:00 Uhr)"),
}


def until_for(choice: str, now: datetime | None = None) -> datetime | None:
    """Ende der Sperre (naiv, UTC wie alle Zeitstempel der DB); None = bis zur Freigabe."""
    if choice not in DURATIONS:
        raise ValueError(_("Ungültige Dauer"))
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


MAX_GRACE_MINUTES = 120


def parse_grace(raw: str) -> int:
    """Vorwarnzeit in Minuten (0 = sofort abmelden)."""
    try:
        minutes = int(raw.strip() or "1")
    except ValueError:
        raise ValueError(_("Vorwarnzeit muss eine ganze Zahl sein")) from None
    if not 0 <= minutes <= MAX_GRACE_MINUTES:
        raise ValueError(_("Vorwarnzeit muss zwischen 0 und {max} Minuten liegen").format(max=MAX_GRACE_MINUTES))
    return minutes


def lock(child: Child, choice: str, grace_minutes: int = 1) -> None:
    child.locked, child.locked_until = True, until_for(choice)
    child.lock_grace_minutes = grace_minutes


def unlock(child: Child) -> None:
    child.locked, child.locked_until = False, None
