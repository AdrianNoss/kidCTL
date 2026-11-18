"""Verwaltungsbereich des Betreibers unter /admin (Etappe 4.2) – getrennt von den Eltern-Seiten."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit, backup, billing, support
from ..config import settings
from ..db import get_platform_db
from ..i18n import N_, _
from ..models import Operator, OperatorSession, SignupCode, utcnow
from ..security import (
    client_ip, csrf_protect_anon, dummy_verify, is_rate_limited, record_failure, totp_locked,
    verify_password, verify_totp,
)
from ..web import render_anon, templates

router = APIRouter(prefix="/admin", dependencies=[Depends(opr.check_network)])


def _render(request: Request, name: str, sess: OperatorSession, status_code: int = 200, **ctx):
    ctx.update(csrf_token=sess.csrf_token, current_parent=None, operator=sess.operator)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# ---------- Anmeldung ----------

@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_platform_db)):
    if opr.get_session(db, request):
        return RedirectResponse("/admin", status_code=303)
    return render_anon(request, "admin_login.html")


@router.post("/login", dependencies=[Depends(csrf_protect_anon)])
def login(request: Request, username: str = Form(""), password: str = Form(""), totp: str = Form(""),
          db: Session = Depends(get_platform_db)):
    ip, username = client_ip(request), username.strip()
    key = f"op:{username}"[:64]   # eigener Zähler, unabhängig von gleichnamigen Eltern-Accounts
    if is_rate_limited(db, "admin", ip, key):
        return render_anon(request, "admin_login.html", status_code=429, username=username,
                           error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))
    op = db.scalar(select(Operator).where(Operator.username == username)) if username else None
    ok = False
    if op is None:
        dummy_verify()
    elif verify_password(password, op.password_hash):
        if totp_locked(db, key):
            return render_anon(request, "admin_login.html", status_code=429, username=username,
                               error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))
        ok = verify_totp(db, op, totp)
        if not ok:
            record_failure(db, "totp", ip, key)
    if not ok:
        record_failure(db, "admin", ip, key)
        return render_anon(request, "admin_login.html", status_code=401, username=username,
                           error=_("Anmeldung fehlgeschlagen."))
    db.commit()   # totp_last_step
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(opr.COOKIE, opr.create_session(db, op), max_age=opr.SESSION_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/admin")
    return resp


@router.post("/logout")
def logout(sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    db.execute(delete(OperatorSession).where(OperatorSession.id == sess.id))   # Support-Ansichten enden mit
    db.commit()
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(opr.COOKIE, path="/admin")
    resp.delete_cookie(support.COOKIE, path="/")
    return resp


# ---------- Übersicht ----------

@router.get("")
def overview(request: Request, sess: OperatorSession = Depends(opr.require_operator),
             db: Session = Depends(get_platform_db)):
    rows = opr.families(db)
    totals = {"families": len(rows), "children": sum(r.children for r in rows),
              "devices": sum(r.devices for r in rows), "paused": sum(1 for r in rows if r.paused_at)}
    return _render(request, "admin_overview.html", sess, families=rows, totals=totals,
                   granted=support.granted_families(db), backups=backup.backups(), backup_error=backup.last_error(),
                   plans=billing.PLANS, statuses=billing.STATUSES)


# ---------- Abo einer Familie ----------

def _family_page(request, db, sess, family_id, status_code=200, **ctx):
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    from ..models import BillingAddress
    row = next((r for r in opr.families(db) if r.id == family_id), None)
    address = db.scalar(select(BillingAddress).where(BillingAddress.family_id == family_id))
    return _render(request, "admin_family.html", sess, status_code, f=f, row=row, st=billing.state(f), address=address,
                   plans=billing.PLANS, statuses=billing.STATUSES, **ctx)


@router.get("/families/{family_id}")
def family_detail(family_id: int, request: Request, sess: OperatorSession = Depends(opr.require_operator),
                  db: Session = Depends(get_platform_db)):
    return _family_page(request, db, sess, family_id, saved=request.query_params.get("saved"))


def _date(value: str):
    from datetime import date, datetime, time
    value = value.strip()
    return datetime.combine(date.fromisoformat(value), time(23, 59)) if value else None


@router.post("/families/{family_id}/name")
def family_name(family_id: int, name: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    before = audit.snapshot(f, ["name"])
    f.name = name.strip()[:128]
    audit.changes(db, audit.actor_of(sess), "family", before, f, family_id=family_id)
    db.commit()
    return RedirectResponse(f"/admin/families/{family_id}?saved=1", status_code=303)


@router.post("/families/{family_id}")
async def family_billing(family_id: int, request: Request, plan: str = Form(...), status: str = Form(...),
                         trial_ends: str = Form(""), paid_until: str = Form(""), provider: str = Form(""),
                         customer: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                         db: Session = Depends(get_platform_db)):
    """Tarif und Abo-Status von Hand setzen (bis ein Zahlungsanbieter angebunden ist); wirkt sofort."""
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    try:
        if plan not in billing.PLANS or status not in billing.STATUSES:
            raise ValueError
        trial, paid = _date(trial_ends), _date(paid_until)
    except ValueError:
        return _family_page(request, db, sess, family_id, 400, error=_("Ungültige Eingabe."))
    before = audit.snapshot(f, billing.AUDIT_FIELDS)
    f.plan, f.trial_ends_at, f.paid_until = plan, trial, paid
    f.billing_provider, f.billing_customer = provider.strip()[:32], customer.strip()[:128]
    billing.set_status(f, status, paid)
    audit.changes(db, audit.actor_of(sess), "subscription", before, f, family_id=family_id)
    db.commit()
    import logging
    logging.getLogger("kidctl.billing").info("Familie %s: Abo gesetzt von %s (%s, %s)", family_id,
                                              sess.operator.username, plan, status)
    await billing.apply(family_id)
    return RedirectResponse(f"/admin/families/{family_id}?saved=1", status_code=303)


# ---------- Support-Ansicht (nur mit Freigabe der Familie) ----------

@router.post("/support/{family_id}")
def support_open(family_id: int, sess: OperatorSession = Depends(opr.operator_csrf),
                 db: Session = Depends(get_platform_db)):
    raw = support.open_view(db, sess, family_id)
    if raw is None:
        raise HTTPException(403, "Keine Freigabe dieser Familie")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(support.COOKIE, raw, max_age=support.VIEW_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/")
    return resp


@router.get("/support/end")
def support_end(request: Request, db: Session = Depends(get_platform_db)):
    """Support-Ansicht beenden (Link im Banner); ohne Betreiber-Anmeldung nötig, löscht nur die eigene Ansicht."""
    support.close_view(db, request)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(support.COOKIE, path="/")
    return resp


# ---------- Einladungscodes ----------

def _codes_page(request, db, sess, status_code=200, **ctx):
    codes = db.scalars(select(SignupCode).order_by(SignupCode.created_at.desc())).all()
    return _render(request, "admin_codes.html", sess, status_code, codes=codes, now=utcnow(), days=opr.CODE_DAYS, **ctx)


@router.get("/codes")
def codes(request: Request, sess: OperatorSession = Depends(opr.require_admin),
          db: Session = Depends(get_platform_db)):
    return _codes_page(request, db, sess)


@router.post("/codes")
def code_create(request: Request, note: str = Form(""), free: str = Form(""),
                sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    code = opr.create_code(db, note, sess.operator_id, "free" if free else "")
    audit.record(db, audit.actor_of(sess), "code", "angelegt", None, (note.strip() or "–") + (" (Kostenlos)" if free else ""))
    db.commit()
    return _codes_page(request, db, sess, new_code=code)


@router.post("/codes/{code_id}/delete")
def code_delete(code_id: int, sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    code = db.get(SignupCode, code_id)
    if code is None:
        raise HTTPException(404)
    if code.used_at is not None:
        raise HTTPException(400, "Eingelöste Codes bleiben zur Nachvollziehbarkeit stehen")
    audit.record(db, audit.actor_of(sess), "code", "gelöscht", code.note or "–", None, object_id=code.id)
    db.delete(code)
    db.commit()
    return RedirectResponse("/admin/codes", status_code=303)


# ---------- Verwaltungsprotokoll ----------

LOG_PAGE = 50
LOG_OBJECTS = {"family": N_("Familie"), "subscription": N_("Abo"), "code": N_("Einladungscode"),
               "ticket": N_("Ticket"), "invoice": N_("Rechnung"), "invoice_settings": N_("Rechnungs-Einstellungen"),
               "price": N_("Preisliste")}


def log_query(family: str = "", actor: str = "", obj: str = ""):
    from ..models import AdminLog
    q = select(AdminLog)
    if family.strip().lstrip("#").isdigit():
        q = q.where(AdminLog.family_id == int(family.strip().lstrip("#")))
    if actor.strip():
        q = q.where(AdminLog.actor.ilike(actor.strip()))
    if obj in LOG_OBJECTS:
        q = q.where(AdminLog.object == obj)
    return q


@router.get("/log")
def admin_log(request: Request, family: str = "", actor: str = "", obj: str = "", page: int = 1,
              sess: OperatorSession = Depends(opr.require_operator), db: Session = Depends(get_platform_db)):
    from sqlalchemy import func
    from ..models import AdminLog
    q = log_query(family, actor, obj)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    page = max(1, page)
    entries = db.scalars(q.order_by(AdminLog.at.desc(), AdminLog.id.desc())
                         .offset((page - 1) * LOG_PAGE).limit(LOG_PAGE)).all()
    actors = sorted(db.scalars(select(AdminLog.actor).distinct()))
    from urllib.parse import urlencode
    params = {k: v for k, v in {"family": family, "actor": actor, "obj": obj}.items() if v}
    return _render(request, "admin_log.html", sess, entries=entries, total=total, page=page,
                   pages=max(1, -(-total // LOG_PAGE)), objects=LOG_OBJECTS, actors=actors,
                   f={"family": family, "actor": actor, "obj": obj}, query=urlencode(params))


# ---------- Eigenes Profil ----------

@router.get("/profile")
def profile(request: Request, sess: OperatorSession = Depends(opr.require_operator)):
    return _render(request, "admin_profile.html", sess, saved=request.query_params.get("saved"),
                   roles=opr.ROLES)


@router.post("/profile")
def profile_save(request: Request, email: str = Form(""), ticket_mails: str = Form(""),
                 sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    from .. import accounts
    from ..models import Operator
    email = email.strip()[:255]
    if email and (err := accounts.email_error(email)):
        return _render(request, "admin_profile.html", sess, 400, error=err, roles=opr.ROLES)
    if ticket_mails and not email:
        return _render(request, "admin_profile.html", sess, 400, roles=opr.ROLES,
                       error=_("Für Ticket-Mails bitte eine E-Mail-Adresse angeben."))
    op = db.get(Operator, sess.operator_id)
    op.email, op.ticket_mails = email, bool(ticket_mails)
    db.commit()
    return RedirectResponse("/admin/profile?saved=1", status_code=303)

