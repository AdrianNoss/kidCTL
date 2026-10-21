"""PWA (Manifest, Service Worker) und Web-Push-Abos der Eltern."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..i18n import _
from ..config import settings
from ..db import get_db
from ..models import ParentSession, PushSubscription
from ..push import send_to_parents_sync
from ..security import csrf_protect, require_session
from ..web import render

STATIC = Path(__file__).resolve().parent.parent / "static"
router = APIRouter()


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    # Aus der Wurzel ausgeliefert, damit der Scope die ganze App umfasst.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/settings")
def settings_page(request: Request, sess: ParentSession = Depends(require_session),
                  db: Session = Depends(get_db)):
    count = len(db.scalars(select(PushSubscription.id)
                           .where(PushSubscription.parent_id == sess.parent_id)).all())
    return render(request, "settings.html", push_enabled=settings.push_enabled,
                  vapid_public_key=settings.vapid_public_key, subscription_count=count,
                  sent=request.query_params.get("sent"))


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=2048, pattern=r"^https://")
    keys: SubscriptionKeys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(max_length=2048)


@router.post("/push/subscribe")
def push_subscribe(sub: SubscriptionIn, sess: ParentSession = Depends(csrf_protect),
                   db: Session = Depends(get_db)):
    if not settings.push_enabled:
        raise HTTPException(503, "Web Push ist nicht konfiguriert")
    existing = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == sub.endpoint))
    if existing is None:
        existing = PushSubscription(endpoint=sub.endpoint)
        db.add(existing)
    # Endpunkt gehört immer zum zuletzt angemeldeten Elternteil auf diesem Gerät
    existing.parent_id = sess.parent_id
    existing.p256dh, existing.auth = sub.keys.p256dh, sub.keys.auth
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/push/unsubscribe")
def push_unsubscribe(sub: UnsubscribeIn, sess: ParentSession = Depends(csrf_protect),
                     db: Session = Depends(get_db)):
    db.execute(delete(PushSubscription).where(PushSubscription.endpoint == sub.endpoint,
                                              PushSubscription.parent_id == sess.parent_id))
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/push/test")
def push_test(sess: ParentSession = Depends(csrf_protect)):
    n = send_to_parents_sync("KidCTL", lambda: _("Test-Benachrichtigung – Push funktioniert."), "/settings",
                             tag="test", parent_id=sess.parent_id)
    return RedirectResponse(f"/settings?sent={n}", status_code=303)
