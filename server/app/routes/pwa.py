"""PWA (Manifest, Service Worker) und Web-Push-Abos der Eltern."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import backup, family_export, support
from ..i18n import N_, _
from ..config import settings
from ..db import get_db
from .. import accounts
from ..models import ROLE_OWNER, Family, Membership, Parent, ParentSession, PushSubscription, utcnow
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
                  sent=request.query_params.get("sent"),
                  backups=[] if settings.saas else backup.backups(), backup_error=None if settings.saas else backup.last_error(),
                  backup_keep=backup.KEEP, backup_hour=backup.BACKUP_HOUR,
                  backup_done=request.query_params.get("backup"), imported=request.query_params.get("imported"),
                  saved=request.query_params.get("saved"),
                  is_owner=_is_owner(db, sess.parent_id), family_empty=family_export.is_empty(db),
                  family=db.scalar(select(Family)),
                  **_support_ctx(db, sess), **ctx)


def _is_owner(db: Session, parent_id) -> bool:
    return db.scalar(select(Membership.role).where(Membership.parent_id == parent_id)) == ROLE_OWNER


def _support_ctx(db: Session, sess) -> dict:
    if not settings.saas:
        return {}
    from .. import billing
    from ..models import Family
    from ..models import BillingAddress
    family = db.scalar(select(Family))
    address = db.scalar(select(BillingAddress))
    return {"billing_address": address, "address_needed": billing.address_needed(family, address) if family else False,
            "support_grant": support.active_grant(db), "support_log": support.log(db),
            "support_durations": support.DURATIONS,
            "billing_state": billing.state(family) if family else None,
            "plan_label": billing.PLANS, "status_label": billing.STATUSES}


def _no_saas() -> None:
    if settings.saas:
        raise HTTPException(404)


def _owner_with_password(request: Request, db: Session, sess, password: str) -> str | None:
    """Export, Import, Löschen: nur Inhaber und mit dem aktuellen Passwort (Fehlertext oder ``None``)."""
    if not _is_owner(db, sess.parent_id):
        raise HTTPException(403, "Nur der Inhaber der Familie")
    error = accounts.check_password(db, request, db.get(Parent, sess.parent_id), password)
    if error:
        db.commit()   # Fehlversuch zählt
    return error


# ---------- Familienname, Rechnungsangaben ----------

@router.post("/settings/family")
def family_rename(name: str = Form(""), sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Name der Familie (nur Inhaber); im Verwaltungsbereich sichtbar."""
    from ..models import Family
    if not _is_owner(db, sess.parent_id):
        raise HTTPException(403, "Nur der Inhaber der Familie")
    db.scalar(select(Family)).name = name.strip()[:128]
    db.commit()
    return RedirectResponse("/settings?saved=family#family", status_code=303)


BILLING_FIELDS = {"name": 128, "street": 128, "postal_code": 16, "city": 64, "country": 64, "email": 255, "vat_id": 32}


@router.post("/settings/billing")
async def billing_address_save(request: Request, sess: ParentSession = Depends(csrf_protect),
                               db: Session = Depends(get_db)):
    """Rechnungsangaben (nur Inhaber, nur SaaS); Plattform-Schlüssel, damit der Betreiber Rechnungen stellen kann."""
    from ..models import BillingAddress
    if not settings.saas:
        raise HTTPException(404)
    if not _is_owner(db, sess.parent_id):
        raise HTTPException(403, "Nur der Inhaber der Familie")
    form = await request.form()
    values = {k: str(form.get(k, "")).strip()[:n] for k, n in BILLING_FIELDS.items()}
    missing = [k for k in BillingAddress.REQUIRED if not values[k]]
    email_error = accounts.email_error(values["email"]) if values["email"] else None
    if missing or email_error:
        return _settings(request, db, sess, 400, billing_form=values,
                         billing_error=email_error or _("Bitte alle Pflichtfelder ausfüllen."))
    address = db.scalar(select(BillingAddress)) or BillingAddress()
    for k, v in values.items():
        setattr(address, k, v)
    db.add(address)
    db.commit()
    return RedirectResponse("/settings?saved=billing#abo", status_code=303)


# ---------- Export, Import, Familie löschen (Etappe 4.5) ----------

@router.post("/settings/export")
def family_export_download(request: Request, current_password: str = Form(""),
                           sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    if error := _owner_with_password(request, db, sess, current_password):
        return _settings(request, db, sess, 400, export_error=error)
    data = family_export.export_zip(db)
    log.warning("Familien-Export (Familie %s) durch Account %s", request.state.family_id, sess.parent_id)
    name = f"kidctl-familie-{utcnow():%Y-%m-%d}.zip"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


IMPORT_ERRORS = {
    "not_empty": N_("Import nur in eine leere Familie (ohne Kinder und Geräte)."),
    "format": N_("Das ist keine KidCTL-Export-Datei."),
    "version": N_("Die Export-Datei stammt von einer neueren KidCTL-Version. Bitte zuerst den Server aktualisieren."),
    "too_large": N_("Die Datei ist zu groß."),
    "content": N_("Die Export-Datei ist beschädigt; es wurde nichts übernommen."),
}


@router.post("/settings/import")
async def family_import(request: Request, file: UploadFile = File(...), current_password: str = Form(""),
                        sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    if error := _owner_with_password(request, db, sess, current_password):
        return _settings(request, db, sess, 400, import_error=error)
    data = await file.read(family_export.MAX_ZIP + 1)
    try:
        if len(data) > family_export.MAX_ZIP:
            raise family_export.ImportError_("too_large")
        counts = family_export.import_zip(db, data)
    except family_export.ImportError_ as e:
        return _settings(request, db, sess, 400, import_error=_(IMPORT_ERRORS.get(str(e), IMPORT_ERRORS["content"])))
    log.warning("Familien-Import (Familie %s): %s", request.state.family_id, counts)
    return RedirectResponse(f"/settings?imported={counts.get('child', 0)}#data", status_code=303)


@router.post("/settings/delete-family")
async def family_delete(request: Request, current_password: str = Form(""), confirm: str = Form(""),
                  sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Familie endgültig löschen (nur SaaS): Schlüssel verwerfen, Accounts und Daten löschen."""
    if not settings.saas:
        raise HTTPException(404)
    if error := _owner_with_password(request, db, sess, current_password):
        return _settings(request, db, sess, 400, delete_error=error)
    if confirm.strip().lower() not in ("löschen", "delete"):
        return _settings(request, db, sess, 400, delete_error=_("Zur Bestätigung bitte LÖSCHEN eingeben."))
    from .. import policy
    from ..models import Device, Family
    from ..tenant import delete_family
    family_id = request.state.family_id
    # Zuerst Ruhemodus: verbundene Agenten heben alle Einschränkungen auf, bevor ihre Familie verschwindet
    db.get(Family, family_id).paused_at = utcnow()
    db.commit()
    await policy.push_policies(db, set(db.scalars(select(Device.id).where(Device.revoked_at.is_(None)))))
    counts = delete_family(db, family_id)
    log.warning("Familie %s durch den Inhaber gelöscht: %s", family_id, counts)
    resp = RedirectResponse("/login?msg=family_deleted", status_code=303)
    resp.delete_cookie("kidctl_session", path="/")
    return resp


@router.post("/settings/support")
def support_grant(hours: int = Form(24), sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Inhaber gibt die Support-Ansicht frei (nur lesend, protokolliert)."""
    if not settings.saas:
        raise HTTPException(404)
    if not _is_owner(db, sess.parent_id):
        raise HTTPException(403, "Nur der Inhaber der Familie")
    if hours not in support.DURATIONS:
        raise HTTPException(400)
    support.grant(db, sess.parent_id, hours)
    return RedirectResponse("/settings#support", status_code=303)


@router.post("/settings/support/revoke")
def support_revoke(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Jedes Elternteil kann die Freigabe sofort beenden – auch eine gerade offene Support-Ansicht."""
    support.revoke(db)
    return RedirectResponse("/settings#support", status_code=303)


@router.post("/settings/backup/download")
def backup_download(request: Request, name: str = Form(""), current_password: str = Form(""),
                    sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Backup herunterladen – enthält Passwort-Hashes und Authenticator-Schlüssel, daher erneut mit Passwort.
    Im SaaS nicht: Die Datenbank enthält alle Familien (Plattform-Backup nur beim Betreiber)."""
    _no_saas()
    parent = db.get(Parent, sess.parent_id)
    info = backup.find(name)
    error = _("Backup nicht gefunden.") if info is None else accounts.check_password(db, request, parent,
                                                                                      current_password)
    if error:
        db.commit()   # Fehlversuch zählt
        return _settings(request, db, sess, 400, download_error=error)
    log.warning("Backup %s heruntergeladen von %s", info.path.name, parent.username)
    return FileResponse(info.path, media_type="application/octet-stream", filename=info.path.name,
                        headers={"Cache-Control": "no-store"})


@router.post("/settings/backup")
def backup_now(sess: ParentSession = Depends(csrf_protect)):
    """Backup sofort erstellen (z. B. vor einem Update)."""
    _no_saas()
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
def push_test(request: Request, sess: ParentSession = Depends(csrf_protect)):
    n = send_to_parents_sync("KidCTL", lambda: _("Test-Benachrichtigung – Push funktioniert."), "/settings",
                             tag="test", parent_id=sess.parent_id, family_id=request.state.family_id)
    return RedirectResponse(f"/settings?sent={n}", status_code=303)
