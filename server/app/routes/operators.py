"""Verwaltungsbereich des Betreibers unter /admin (Etappe 4.2) – getrennt von den Eltern-Seiten."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import backup, support
from ..config import settings
from ..db import get_platform_db
from ..i18n import _
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
                   granted=support.granted_families(db), backups=backup.backups(), backup_error=backup.last_error())


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
def codes(request: Request, sess: OperatorSession = Depends(opr.require_operator),
          db: Session = Depends(get_platform_db)):
    return _codes_page(request, db, sess)


@router.post("/codes")
def code_create(request: Request, note: str = Form(""), sess: OperatorSession = Depends(opr.operator_csrf),
                db: Session = Depends(get_platform_db)):
    code = opr.create_code(db, note, sess.operator_id)
    return _codes_page(request, db, sess, new_code=code)


@router.post("/codes/{code_id}/delete")
def code_delete(code_id: int, sess: OperatorSession = Depends(opr.operator_csrf),
                db: Session = Depends(get_platform_db)):
    code = db.get(SignupCode, code_id)
    if code is None:
        raise HTTPException(404)
    if code.used_at is not None:
        raise HTTPException(400, "Eingelöste Codes bleiben zur Nachvollziehbarkeit stehen")
    db.delete(code)
    db.commit()
    return RedirectResponse("/admin/codes", status_code=303)
