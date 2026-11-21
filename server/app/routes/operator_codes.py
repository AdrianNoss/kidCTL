"""Verwaltungsbereich: Einladungscodes (nur Admin)."""

from __future__ import annotations

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit
from ..db import get_platform_db
from ..models import OperatorSession, SignupCode, utcnow
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Einladungscodes ----------

def _codes_page(request, db, sess, status_code=200, **ctx):
    codes = db.scalars(select(SignupCode).order_by(SignupCode.created_at.desc())).all()
    return _render(request, "admin_codes.html", sess, status_code, codes=codes, now=utcnow(), days=opr.CODE_DAYS,
                   mask=opr.mask_email, **ctx)


@router.get("/codes")
def codes(request: Request, sess: OperatorSession = Depends(opr.require_admin),
          db: Session = Depends(get_platform_db)):
    return _codes_page(request, db, sess)


@router.post("/codes")
def code_create(request: Request, note: str = Form(""), free: str = Form(""), email: str = Form(""),
                lang: str = Form("de"), sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    from .. import accounts
    email = email.strip()
    if email and (err := accounts.email_error(email)):
        return _codes_page(request, db, sess, 400, error=err, form={"note": note, "email": email})
    code = opr.create_code(db, note, sess.operator_id, "free" if free else "", email or None, lang)
    audit.record(db, audit.actor_of(sess), "code", "angelegt", None, (note.strip() or "–") + (" (Kostenlos)" if free else "")
                 + (" · per Mail" if email else ""))
    db.commit()
    sent = None
    if email:
        sent = opr.send_invite(db, opr.find_code(db, code), code)
    return _codes_page(request, db, sess, new_code=code, sent=sent, sent_to=opr.mask_email(email))


@router.post("/codes/{code_id}/resend")
def code_resend(code_id: int, request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    """Neuer Code an dieselbe Adresse; der alte wird ungültig (gespeichert ist nur sein Hash)."""
    old = db.get(SignupCode, code_id)
    if old is None or not old.email:
        raise HTTPException(404)
    if old.used_at is not None or old.expires_at < utcnow():
        raise HTTPException(400, "Nur offene Codes")
    note, plan, email, lang = old.note, old.plan, old.email, old.lang
    db.delete(old)
    code = opr.create_code(db, note, sess.operator_id, plan, email, lang)
    audit.record(db, audit.actor_of(sess), "code", "erneut gesendet", note or "–", None, object_id=code_id)
    db.commit()
    sent = opr.send_invite(db, opr.find_code(db, code), code)
    return _codes_page(request, db, sess, new_code=code, sent=sent, sent_to=opr.mask_email(email))


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
