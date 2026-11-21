"""Verwaltungsbereich: Erscheinungsbild (Whitelabel), Anbieterangaben, Startseite."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit
from ..db import get_platform_db
from ..i18n import N_, _
from ..models import OperatorSession, utcnow
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Erscheinungsbild (Whitelabel) ----------

BRAND_ERRORS = {"too_big": N_("Das Bild ist zu groß (höchstens 2 MB und 25 Megapixel)."),
                "format": N_("Bitte ein Bild als PNG, JPEG, WebP, GIF oder ICO hochladen.")}


def _branding_row(db: Session):
    from ..models import Branding
    row = db.get(Branding, 1)
    if row is None:
        row = Branding(id=1, name="")
        db.add(row)
        db.flush()
    return row


def _branding_page(request, db, sess, status_code: int = 200, **ctx):
    from .. import branding
    from ..models import Branding
    row = db.get(Branding, 1)
    return _render(request, "admin_branding.html", sess, status_code, row=row, b=branding.current(),
                   max_mb=branding.MAX_UPLOAD // (1024 * 1024), saved=request.query_params.get("saved"), **ctx)


@router.get("/branding")
def branding_page(request: Request, sess: OperatorSession = Depends(opr.require_admin),
                  db: Session = Depends(get_platform_db)):
    return _branding_page(request, db, sess)


@router.post("/branding")
async def branding_save(request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                        db: Session = Depends(get_platform_db)):
    """Name, Logo und App-Icon speichern; jede Änderung ins Verwaltungsprotokoll (Bilder nur als „neu“/„entfernt“)."""
    from .. import branding
    form = await request.form()
    row = _branding_row(db)
    actor = audit.actor_of(sess)
    name = " ".join(str(form.get("name", "")).split())[:64]
    if name != row.name:
        audit.record(db, actor, "branding", "name", row.name, name)
        row.name = name
    logo_only = bool(form.get("logo_only"))
    images = {}
    for kind, normalize in (("logo", branding.normalize_logo), ("icon", branding.normalize_icon)):
        upload = form.get(kind)
        data = await upload.read(branding.MAX_UPLOAD + 1) if upload is not None and hasattr(upload, "read") else b""
        if data:
            try:
                images[kind] = normalize(data)
            except branding.ImageError as e:
                db.rollback()
                return _branding_page(request, db, sess, 400, error=_(BRAND_ERRORS[str(e)]))
        elif form.get(f"remove_{kind}") and getattr(row, kind) is not None:
            images[kind] = None
    for kind, data in images.items():
        audit.record(db, actor, "branding", kind, None, _("neu") if data else _("entfernt"))
        setattr(row, kind, data)
    logo_only = logo_only and row.logo is not None
    if logo_only != row.logo_only:
        audit.record(db, actor, "branding", "logo_only", row.logo_only, logo_only)
        row.logo_only = logo_only
    row.updated_at = utcnow()
    db.commit()
    branding.invalidate()
    return RedirectResponse("/admin/branding?saved=1", status_code=303)


PROVIDER_FIELDS = {"provider_name": 128, "provider_address": 255, "provider_email": 255, "imprint_url": 255,
                   "privacy_url": 255, "news_url": 255}


@router.post("/branding/provider")
async def provider_save(request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                        db: Session = Depends(get_platform_db)):
    """Anbieter-Angaben für die Fußzeile der Mails (leere Felder entfallen); URLs nur http(s)."""
    form = await request.form()
    row = _branding_row(db)
    values = {}
    for field, n in PROVIDER_FIELDS.items():
        value = " ".join(str(form.get(field, "")).split())[:n]
        if field.endswith("_url") and value and not value.lower().startswith(("https://", "http://")):
            db.rollback()
            return _branding_page(request, db, sess, 400, error=_("Links bitte mit https:// angeben."))
        if field == "provider_email" and value:
            from .. import accounts
            if err := accounts.email_error(value):
                db.rollback()
                return _branding_page(request, db, sess, 400, error=err)
        values[field] = value
    before = audit.snapshot(row, list(PROVIDER_FIELDS))
    for field, value in values.items():
        setattr(row, field, value)
    audit.changes(db, audit.actor_of(sess), "branding", before, row)
    db.commit()
    return RedirectResponse("/admin/branding?saved=provider#anbieter", status_code=303)


HOMEPAGE_FIELDS = {"homepage_url": 255, "homepage_label_de": 64, "homepage_label_en": 64}


@router.post("/branding/homepage")
async def homepage_save(request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                        db: Session = Depends(get_platform_db)):
    """Link unter den Anmeldeseiten (nur https); leer: Projektseite."""
    from .. import branding
    form = await request.form()
    values = {f: " ".join(str(form.get(f, "")).split())[:n] for f, n in HOMEPAGE_FIELDS.items()}
    url = values["homepage_url"]
    if url and (not url.lower().startswith("https://") or len(url) <= len("https://")):
        return _branding_page(request, db, sess, 400, error=_("Die Homepage bitte mit https:// angeben."))
    row = _branding_row(db)
    before = audit.snapshot(row, list(HOMEPAGE_FIELDS))
    for field, value in values.items():
        setattr(row, field, value)
    audit.changes(db, audit.actor_of(sess), "branding", before, row)
    db.commit()
    branding.invalidate()
    return RedirectResponse("/admin/branding?saved=homepage#homepage", status_code=303)
