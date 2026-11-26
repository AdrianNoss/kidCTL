"""PWA (Manifest, Service Worker) und Web-Push-Abos der Eltern."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import backup, branding, family_export, support
from ..i18n import N_, _
from ..config import settings
from ..db import get_db
from .. import accounts
from ..models import ROLE_OWNER, Family, HolidayRegion, Membership, Parent, ParentSession, PushSubscription, utcnow
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
    """PWA-Manifest mit Name und Icons aus dem Branding (Whitelabel)."""
    import json
    from fastapi.responses import Response
    from .. import branding
    b = branding.current()
    data = json.loads((STATIC / "manifest.webmanifest").read_text())
    data["name"] = b.name
    data["short_name"] = b.name[:12]
    data["icons"] = [{"src": b.icon_url(size), "sizes": f"{size}x{size}", "type": "image/png",
                      "purpose": "any" if b.has_icon else "any maskable"} for size in (192, 512)]
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json",
                    headers={"Cache-Control": "no-cache"})


_DEFAULT_ICONS = {512: "icon-512.png", 192: "icon-192.png", 180: "apple-touch-icon.png", 32: "icon-192.png"}


@router.get("/brand/icon-{size}.png", include_in_schema=False)
def brand_icon(size: int):
    """App-Icon/Favicon: eigenes Icon in der gewünschten Größe, sonst das KidCTL-Icon."""
    from fastapi.responses import Response
    from .. import branding
    if size not in branding.ICON_SIZES:
        raise HTTPException(404)
    if branding.current().has_icon and (data := branding.image("icon", size)):
        return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    return FileResponse(STATIC / _DEFAULT_ICONS[size], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/brand/logo.png", include_in_schema=False)
def brand_logo():
    from fastapi.responses import Response
    from .. import branding
    data = branding.image("logo") if branding.current().has_logo else None
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


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
                  family=db.scalar(select(Family)), **_holiday_ctx(db), **_nintendo_ctx(db),
                  **_support_ctx(db, sess), **ctx)


def _holiday_ctx(db: Session) -> dict:
    from .. import i18n, schedule as sch, vacations
    from ..models import Vacation
    region = db.scalar(select(HolidayRegion))
    return {"vacations": db.scalars(select(Vacation).where(Vacation.last_day >= sch.today_local())
                                    .order_by(Vacation.first_day, Vacation.id)).all(),
            "holiday_region": region, "holiday_countries": vacations.COUNTRIES,
            "holiday_subdivs": {c: vacations.subdivisions(c) for c in vacations.COUNTRIES},
            "weekday_names": i18n.weekday_names(), "vacation_today": sch.today_local()}


def _nintendo_ctx(db: Session) -> dict:
    if not settings.nintendo:
        return {}
    from .. import nintendo
    from ..models import Child, NintendoConsole
    row = nintendo.link(db)
    return {"nintendo_link": row, "nintendo_login_url": nintendo.pending_url(row),
            "nintendo_consoles": db.scalars(select(NintendoConsole).order_by(NintendoConsole.id)).all(),
            "nintendo_children": db.scalars(select(Child).order_by(Child.id)).all()}


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
    from .. import invoices
    from ..models import Invoice
    family_invoices = db.scalars(select(Invoice).where(Invoice.family_id == family.id, Invoice.status.in_(invoices.ISSUED))
                                 .order_by(Invoice.issue_date.desc(), Invoice.seq.desc())).all() if family else []
    return {"family_invoices": family_invoices, "eur": invoices.eur, "invoice_statuses": invoices.STATUSES,
            "billing_address": address, "address_needed": billing.address_needed(family, address) if family else False,
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


# ---------- Hinweis „Neu in Version …“ ----------

@router.post("/info/seen")
def info_seen(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Hinweis auf der Übersicht bestätigen: bis zur nächsten Server-Version ausblenden."""
    from .. import about
    parent = db.get(Parent, sess.parent_id)
    parent.seen_version = about.VERSION
    db.commit()
    return RedirectResponse("/", status_code=303)


# ---------- Rechnungen der Familie ----------

@router.get("/settings/invoices/{invoice_id}.pdf")
def family_invoice_pdf(invoice_id: int, request: Request, sess: ParentSession = Depends(require_session),
                       db: Session = Depends(get_db)):
    """PDF einer ausgestellten Rechnung – nur der eigenen Familie (Rechnungen sind nicht familienbezogen gefiltert)."""
    from .. import invoices
    from ..models import Invoice
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.family_id != request.state.family_id or inv.status not in invoices.ISSUED:
        raise HTTPException(404)
    return Response(invoices.pdf_bytes(inv), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv.number}.pdf"', "Cache-Control": "no-store"})


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


# ---------- Ferien und Feiertage (alle Eltern) ----------

async def _push_family(db: Session) -> None:
    from .. import policy
    from ..models import Device
    await policy.push_policies(db, set(db.scalars(select(Device.id).where(Device.revoked_at.is_(None)))))


@router.post("/settings/vacations")
async def vacation_add(request: Request, name: str = Form(""), first_day: str = Form(""), last_day: str = Form(""),
                       sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    from .. import vacations
    from ..models import Vacation
    try:
        name, first, last = vacations.parse_vacation(name, first_day, last_day)
    except vacations.VacationError as e:
        return _settings(request, db, sess, status_code=400, vacation_error=str(e),
                         vacation_form={"name": name, "first_day": first_day, "last_day": last_day})
    db.add(Vacation(name=name, first_day=first, last_day=last))
    db.commit()
    await _push_family(db)
    return RedirectResponse("/settings?saved=vacation#ferien", status_code=303)


@router.post("/settings/vacations/{vacation_id}/delete")
async def vacation_delete(vacation_id: int, sess: ParentSession = Depends(csrf_protect),
                          db: Session = Depends(get_db)):
    from ..models import Vacation
    vac = db.get(Vacation, vacation_id)
    if vac is None:
        raise HTTPException(404)
    db.delete(vac)
    db.commit()
    await _push_family(db)
    return RedirectResponse("/settings?saved=vacation#ferien", status_code=303)


@router.post("/settings/holidays")
async def holidays_set(request: Request, region: str = Form(""), as_weekday: str = Form("6"),
                       sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Feiertage: ``region`` = „“ (keine), „DE:“ (nur landesweit) oder „DE:BY“."""
    from .. import vacations
    country, _sep, subdiv = region.partition(":")
    try:
        vacations.set_region(db, country, subdiv, as_weekday)
    except vacations.VacationError as e:
        db.rollback()
        return _settings(request, db, sess, status_code=400, holiday_error=str(e))
    db.commit()
    await _push_family(db)
    return RedirectResponse("/settings?saved=holidays#ferien", status_code=303)


# ---------- Newsletter (Opt-in je Familie) ----------

@router.post("/settings/newsletter")
def newsletter_toggle(subscribe: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                      db: Session = Depends(get_db)):
    """Neuigkeiten per Mail für die ganze Familie an- oder abbestellen (jedes Elternteil darf das)."""
    from ..models import Family, utcnow
    family = db.scalar(select(Family))
    if subscribe and family.newsletter_at is None:
        family.newsletter_at = utcnow()
    elif not subscribe:
        family.newsletter_at = None
    db.commit()
    return RedirectResponse("/settings?saved=newsletter#newsletter", status_code=303)


@router.get("/newsletter/unsubscribe/{family_id}/{sig}", include_in_schema=False)
def newsletter_unsubscribe_page(family_id: int, sig: str, request: Request):
    """Abmeldelink aus der Mail: erst bestätigen (Mail-Scanner rufen Links auf, ohne zu klicken)."""
    from .. import newsletter
    from ..web import render_anon
    if not newsletter.valid_signature(family_id, sig):
        raise HTTPException(404)
    return render_anon(request, "newsletter_unsubscribe.html", family_id=family_id, sig=sig)


@router.post("/newsletter/unsubscribe/{family_id}/{sig}", include_in_schema=False)
def newsletter_unsubscribe(family_id: int, sig: str, request: Request):
    """Abmelden – auch „mit einem Klick“ aus dem Mailprogramm (RFC 8058, ohne Formular-Token; die Signatur genügt)."""
    from .. import newsletter
    from ..web import render_anon
    if not newsletter.valid_signature(family_id, sig):
        raise HTTPException(404)
    newsletter.unsubscribe(family_id)
    return render_anon(request, "newsletter_unsubscribe.html", done=True)


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
    from .. import audit
    audit.record(db, "Familie", "family", "gelöscht", "durch den Inhaber", None, family_id=family_id)
    db.commit()
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
    n = send_to_parents_sync(branding.name(), lambda: _("Test-Benachrichtigung – Push funktioniert."), "/settings",
                             tag="test", parent_id=sess.parent_id, family_id=request.state.family_id)
    return RedirectResponse(f"/settings?sent={n}", status_code=303)
