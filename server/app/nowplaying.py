"""„Spielt gerade“: zuletzt gemeldete laufende Spiele je Gerät (nur im Speicher).

Der Agent meldet sie mit jedem Programmzeit-Bericht (etwa jede Minute). Nach einem Neustart des
Servers ist die Anzeige bis zur nächsten Meldung leer; ohne frische Meldung oder bei getrenntem Gerät
wird nichts angezeigt.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from .models import DeviceUser, GameSession
from .ws import manager

STALE_SECONDS = 150.0
# Meldung innerhalb dieser Lücke setzt eine Sitzung fort (Bericht ~ jede Minute, Server-Neustart)
SESSION_GAP = timedelta(minutes=3)
_state: dict[int, tuple[float, list[p.PlayingEntry]]] = {}


def update(device_id: int, entries: list[p.PlayingEntry], db: Session | None = None,
           now: datetime | None = None) -> None:
    _state[device_id] = (time.monotonic(), list(entries))
    if db is not None and entries:
        record_sessions(db, device_id, entries, now or datetime.now(timezone.utc).replace(tzinfo=None))


def _clean(name: str) -> str:
    return "".join(ch for ch in name if ch.isprintable()).strip()[:255]


def record_sessions(db: Session, device_id: int, entries: list[p.PlayingEntry], now: datetime) -> None:
    """Laufende Spiele als Sitzungen fortschreiben (nur zugeordnete User, nur Spiel-Schlüssel)."""
    mapping = dict(db.execute(select(DeviceUser.username, DeviceUser.child_id)
                              .where(DeviceUser.device_id == device_id, DeviceUser.child_id.is_not(None))).all())
    for e in {(e.username, e.key): e for e in entries}.values():
        child = mapping.get(e.username)
        if child is None or not e.key.startswith("game:"):
            continue
        s = db.scalar(select(GameSession).where(
            GameSession.device_id == device_id, GameSession.username == e.username, GameSession.key == e.key,
            GameSession.last_seen_at >= now - SESSION_GAP).order_by(GameSession.last_seen_at.desc()).limit(1))
        if s is None:
            db.add(GameSession(child_id=child, device_id=device_id, username=e.username, key=e.key,
                               name=_clean(e.name) or e.key.rsplit(":", 1)[-1], started_at=now, last_seen_at=now))
        else:
            s.last_seen_at = now
            if e.name:
                s.name = _clean(e.name)


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
