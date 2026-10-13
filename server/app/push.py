"""Web Push (VAPID) an alle Eltern-Abos."""

from __future__ import annotations

import asyncio
import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select

from .config import settings
from .db import SessionLocal
from .models import PushSubscription

log = logging.getLogger("kidctl.push")

TTL = 24 * 3600  # Sekunden, wie lange der Push-Dienst zustellt, falls das Handy offline ist


def _send_one(sub: PushSubscription, payload: str) -> bool:
    """Sendet synchron. Gibt False zurück, wenn das Abo nicht mehr gültig ist."""
    try:
        webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=TTL,
            timeout=10,
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return False
        log.warning("Push an Abo %s fehlgeschlagen: %s", sub.id, e)
        return True
    except Exception as e:  # Netzwerkfehler: Abo behalten, beim nächsten Mal erneut versuchen
        log.warning("Push an Abo %s fehlgeschlagen: %s", sub.id, type(e).__name__)
        return True


def send_to_parents_sync(title: str, body: str, url: str = "/", tag: str | None = None,
                         parent_id: int | None = None) -> int:
    """Sendet an alle Abos (oder nur die eines Elternteils). Gibt die Anzahl Zustellungen zurück."""
    if not settings.push_enabled:
        return 0
    payload = json.dumps({"title": title, "body": body[:300], "url": url, "tag": tag})
    with SessionLocal() as db:
        q = select(PushSubscription)
        if parent_id is not None:
            q = q.where(PushSubscription.parent_id == parent_id)
        subs = db.scalars(q).all()
        gone = [s.id for s in subs if not _send_one(s, payload)]
        if gone:
            db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
            db.commit()
            log.info("%d abgelaufene Push-Abos entfernt", len(gone))
    return len(subs) - len(gone)


_tasks: set[asyncio.Task] = set()


def notify_parents(title: str, body: str, url: str = "/", tag: str | None = None) -> None:
    """Nicht blockierend aus async-Code: Versand läuft im Thread-Pool."""
    if not settings.push_enabled:
        return
    task = asyncio.get_running_loop().create_task(
        asyncio.to_thread(send_to_parents_sync, title, body, url, tag))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
