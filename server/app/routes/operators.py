"""Verwaltungsbereich des Betreibers unter /admin (Etappe 4.2): Anmeldung, Abmeldung, Passkey-Anmeldung, Menü."""

from __future__ import annotations

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import loginticket, passkeys, support
from ..config import settings
from ..db import get_platform_db
from ..i18n import _
from ..models import Operator, OperatorSession
from ..security import (
    begin_totp_attempt, client_ip, csrf_protect_anon, dummy_verify, is_rate_limited, record_failure, totp_attempt_ok,
    totp_locked, verify_password, verify_totp,
)
from ..web import render_anon
from . import profile_shared as ps
from .operator_base import new_router, render as _render

router = new_router()
_json = ps.json_response


# ---------- Anmeldung ----------

@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_platform_db)):
    if opr.get_session(db, request):
        return RedirectResponse("/admin", status_code=303)
    notice = _("Die Anmeldung wurde abgebrochen oder ist abgelaufen. Bitte noch einmal anmelden.") \
        if request.query_params.get("msg") == "expired" else None
    return render_anon(request, "admin_login.html", notice=notice)


@router.post("/login", dependencies=[Depends(csrf_protect_anon)])
def login(request: Request, username: str = Form(""), password: str = Form(""),
          db: Session = Depends(get_platform_db)):
    """Schritt 1: Passwort (wie bei Eltern, eigene Zähler und Tickets)."""
    ip, username = client_ip(request), username.strip()
    key = f"op:{username}"[:64]   # eigener Zähler, unabhängig von gleichnamigen Eltern-Accounts
    if is_rate_limited(db, "admin", ip, key):
        return render_anon(request, "admin_login.html", status_code=429, username=username,
                           error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))
    op = db.scalar(select(Operator).where(Operator.username == username)) if username else None
    if op is None:
        dummy_verify()
    elif verify_password(password, op.password_hash):
        if totp_locked(db, key):
            return render_anon(request, "admin_login.html", status_code=429, username=username,
                               error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))
        resp = RedirectResponse("/admin/login/code", status_code=303)
        loginticket.set_cookie(resp, "operator", loginticket.create(db, "operator", op.id))
        return resp
    record_failure(db, "admin", ip, key)
    return render_anon(request, "admin_login.html", status_code=401, username=username,
                       error=_("Anmeldung fehlgeschlagen."))


def _login_expired():
    resp = RedirectResponse("/admin/login?msg=expired", status_code=303)
    loginticket.clear_cookie(resp, "operator")
    return resp


def _code_page(request, status_code=200, error=None):
    return render_anon(request, "login_code.html", status_code=status_code, action="/admin/login/code",
                       back="/admin/login", admin=True, error=error)


@router.get("/login/code")
def login_code_form(request: Request, db: Session = Depends(get_platform_db)):
    if loginticket.get(db, request, "operator") is None:
        return _login_expired()
    return _code_page(request)


@router.post("/login/code", dependencies=[Depends(csrf_protect_anon)])
def login_code(request: Request, totp: str = Form(""), db: Session = Depends(get_platform_db)):
    """Schritt 2: Authenticator-Code zum Ticket."""
    ticket = loginticket.get(db, request, "operator")
    op = db.get(Operator, ticket.account_id) if ticket else None
    if op is None:
        return _login_expired()
    ip, key = client_ip(request), f"op:{op.username}"[:64]
    if not loginticket.claim(db, ticket):
        return _login_expired()
    attempt = begin_totp_attempt(db, ip, key)
    if attempt is None:
        return _code_page(request, 429, _("Zu viele Fehlversuche. Bitte später erneut versuchen."))
    if not verify_totp(db, op, totp):
        record_failure(db, "admin", ip, key)
        if loginticket.failed(db, ticket):
            return _login_expired()
        return _code_page(request, 401, _("Code falsch. Bitte erneut versuchen."))
    totp_attempt_ok(db, attempt)
    loginticket.consume(db, ticket)
    db.commit()   # totp_last_step
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(opr.COOKIE, opr.create_session(db, op), max_age=opr.SESSION_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/admin")
    loginticket.clear_cookie(resp, "operator")
    return resp


@router.post("/logout")
def logout(sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    db.execute(delete(OperatorSession).where(OperatorSession.id == sess.id))   # Support-Ansichten enden mit
    db.commit()
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(opr.COOKIE, path="/admin")
    resp.delete_cookie(support.COOKIE, path="/")
    return resp


# ---------- Passkey-Anmeldung (Betreiber) ----------

@router.post("/login/passkey/options", dependencies=[Depends(csrf_protect_anon)])
def login_passkey_options(request: Request, db: Session = Depends(get_platform_db)):
    if is_rate_limited(db, "admin", client_ip(request)):
        return _json({"error": _("Zu viele Fehlversuche. Bitte später erneut versuchen.")}, 429)
    options, token = passkeys.authentication_options(db, "operator")
    resp = _json({"options": options})
    passkeys.set_cookie(resp, "operator", token)
    return resp


@router.post("/login/passkey", dependencies=[Depends(csrf_protect_anon)])
def login_passkey(request: Request, credential: str = Form(""), db: Session = Depends(get_platform_db)):
    ip = client_ip(request)
    if is_rate_limited(db, "admin", ip):
        return _json({"error": _("Zu viele Fehlversuche. Bitte später erneut versuchen.")}, 429)
    try:
        pk = passkeys.authenticate(db, passkeys.cookie(request, "operator"), credential, "operator")
    except passkeys.PasskeyError as e:
        passkeys.log.info("Passkey-Anmeldung (Betreiber) abgelehnt: %s", e)
        record_failure(db, "admin", ip)
        return _json({"error": _("Anmeldung mit Passkey fehlgeschlagen.")}, 400)
    op = db.get(Operator, pk.account_id)
    if op is None:
        return _json({"error": _("Anmeldung mit Passkey fehlgeschlagen.")}, 400)
    resp = _json({"redirect": "/admin"})
    resp.set_cookie(opr.COOKIE, opr.create_session(db, op), max_age=opr.SESSION_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/admin")
    resp.delete_cookie(passkeys.COOKIES["operator"][0], path="/admin")
    return resp


# ---------- Menü ----------

@router.get("/more")
def more(request: Request, sess: OperatorSession = Depends(opr.require_operator)):
    """Handy: übrige Bereiche, Profil und Abmelden (untere Leiste hat nur Platz für drei Einträge)."""
    return _render(request, "admin_more.html", sess)
