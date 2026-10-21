"""PWA (Manifest, Service Worker) und Web-Push-Abos der Eltern."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import backup
from ..i18n import _
from ..config import settings
from ..db import get_db
from .. import accounts
from ..models import Parent, ParentSession, PushSubscription
from ..push import send_to_parents_sync
from ..security import csrf_protect, require_session
from ..web import render

STATIC = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("kidctl.backup")
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
    return _settings(request, db, sess)


def _settings(request: Request, db: Session, sess: ParentSession, status_code: int = 200, **ctx):
    count = len(db.scalars(select(PushSubscription.id)
                           .where(PushSubscription.parent_id == sess.parent_id)).all())
    return render(request, "settings.html", status_code=status_code, push_enabled=settings.push_enabled,
                  vapid_public_key=settings.vapid_public_key, subscription_count=count,
                  sent=request.query_params.get("sent"), backups=backup.backups(), backup_error=backup.last_error(),
                  backup_keep=backup.KEEP, backup_hour=backup.BACKUP_HOUR,
                  backup_done=request.query_params.get("backup"), **ctx)


@router.post("/settings/backup/download")
def backup_download(request: Request, name: str = Form(""), current_password: str = Form(""),
                    sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Backup herunterladen – enthält Passwort-Hashes und Authenticator-Schlüssel, daher erneut mit Passwort."""
    parent = db.get(Parent, sess.parent_id)
    info = backup.find(name)
    error = _("Backup nicht gefunden.") if info is None else accounts.check_password(db, request, parent,
                                                                                      current_password)
    if error:
        db.commit()   # Fehlversuch zählt
        return _settings(request, db, sess, 400, download_error=error)
    log.warning("Backup %s heruntergeladen von %s", info.path.name, parent.username)
    return FileResponse(info.path, media_type="application/gzip", filename=info.path.name,
                        headers={"Cache-Control": "no-store"})


@router.post("/settings/backup")
def backup_now(sess: ParentSession = Depends(csrf_protect)):
    """Backup sofort erstellen (z. B. vor einem Update)."""
    try:
        backup.run()
        result = "ok"
    except Exception:
        log.exception("Backup fehlgeschlagen")
        result = "error"
    return RedirectResponse(f"/settings?backup={result}#backup", status_code=303)


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
