"""„Spielt gerade“: zuletzt gemeldete laufende Spiele je Gerät (nur im Speicher).

Der Agent meldet sie mit jedem Programmzeit-Bericht (etwa jede Minute). Nach einem Neustart des
Servers ist die Anzeige bis zur nächsten Meldung leer; ohne frische Meldung oder bei getrenntem Gerät
wird nichts angezeigt.
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from .models import DeviceUser
from .ws import manager

STALE_SECONDS = 150.0
_state: dict[int, tuple[float, list[p.PlayingEntry]]] = {}


def update(device_id: int, entries: list[p.PlayingEntry]) -> None:
    _state[device_id] = (time.monotonic(), list(entries))


def for_children(db: Session) -> dict[int, list[str]]:
    """Kind-ID -> Namen der gerade laufenden Spiele (über alle Geräte, ohne Doppelte)."""
    now = time.monotonic()
    fresh = {d: entries for d, (at, entries) in _state.items()
             if now - at <= STALE_SECONDS and manager.is_online(d) and entries}
    if not fresh:
        return {}
    mapping = {(d, u): c for d, u, c in db.execute(
        select(DeviceUser.device_id, DeviceUser.username, DeviceUser.child_id)
        .where(DeviceUser.device_id.in_(fresh), DeviceUser.child_id.is_not(None)))}
    out: dict[int, dict[str, str]] = {}
    for d, entries in fresh.items():
        for e in entries:
            if (child := mapping.get((d, e.username))) is not None:
                out.setdefault(child, {})[e.key] = e.name
    return {c: sorted(games.values(), key=str.lower) for c, games in out.items()}
