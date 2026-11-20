"""Web Push (VAPID) an alle Eltern-Abos.

Titel und Text dürfen Funktionen ohne Argumente sein: Sie werden je Elternteil in dessen Sprache
ausgewertet (``i18n.parent_language``)."""

from __future__ import annotations

import asyncio
import threading
import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select

from . import branding, i18n
from .config import settings
from .tenant import family_ids, family_session, platform_session
from .models import Parent, PushSubscription

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


def _text(value) -> str:
    return value() if callable(value) else value


def send_to_parents_sync(title, body, url: str = "/", tag: str | None = None, parent_id: int | None = None, *,
                         family_id: int) -> int:
    """Sendet an alle Abos der Eltern von ``family_id`` (oder nur eines Elternteils). Anzahl Zustellungen."""
    if not settings.push_enabled:
        return 0
    with family_session(family_id) as db:
        q = select(PushSubscription)
        if parent_id is not None:
            q = q.where(PushSubscription.parent_id == parent_id)
        subs = db.scalars(q).all()
        payloads: dict[int, str] = {}
        for pid in {s.parent_id for s in subs}:
            parent = db.get(Parent, pid)
            with i18n.use(i18n.parent_language(parent) if parent else "de"):
                payloads[pid] = json.dumps({"title": branding.apply(_text(title)), "body": branding.apply(_text(body))[:300],
                                            "url": url, "tag": tag})
        gone = [s.id for s in subs if not _send_one(s, payloads[s.parent_id])]
        if gone:
            db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
            db.commit()
            log.info("%d abgelaufene Push-Abos entfernt", len(gone))
    return len(subs) - len(gone)


_tasks: set[asyncio.Task] = set()


def notify_parents(title, body, url: str = "/", tag: str | None = None, *, family_id: int) -> None:
    """Nicht blockierend: Versand läuft im Thread-Pool (aus async-Code) bzw. in einem eigenen Thread (aus einem
    Worker-Thread ohne Event-Loop, z. B. Agenten-Nachrichten)."""
    if not settings.push_enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(target=send_to_parents_sync, args=(title, body, url, tag), kwargs={"family_id": family_id},
                         daemon=True, name="kidctl-push").start()
        return
    task = loop.create_task(
        asyncio.to_thread(send_to_parents_sync, title, body, url, tag, family_id=family_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def send_platform_alert(title, body, url: str = "/", tag: str | None = None) -> int:
    """Hinweis zum Betrieb (z. B. Backup). Einzelbetrieb: an die Eltern der einzigen Familie; mit mehreren
    Familien nur ins Log, bis der Verwaltungsbereich des Betreibers Hinweise bekommt (Etappe 4)."""
    with platform_session() as db:
        families = family_ids(db)
    if len(families) == 1:
        return send_to_parents_sync(title, body, url, tag, family_id=families[0])
    log.warning("Betriebshinweis (nicht an Familien gesendet): %s – %s", _text(title), _text(body))
    return 0
