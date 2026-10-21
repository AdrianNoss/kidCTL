from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import accounts
from .. import i18n
from ..i18n import N_, _
from ..config import settings
from ..db import get_db
from ..models import Parent, ParentSession, utcnow
from ..security import (
    SESSION_COOKIE, client_ip, create_session, csrf_protect, csrf_protect_anon, dummy_verify,
    get_session, hash_password, is_rate_limited, record_failure, set_cookie, totp_qr_svg, verify_password,
    verify_totp,
)
from ..web import render_anon

router = APIRouter()


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_session(db, request):
        return RedirectResponse("/", status_code=303)
    notice = LOGIN_NOTICES.get(request.query_params.get("msg", ""))
    return render_anon(request, "login.html", notice=_(notice) if notice else None)


LOGIN_NOTICES = {
    "reset": N_("Passwort geändert. Bitte mit dem neuen Passwort und dem Authenticator-Code anmelden."),
    "totp": N_("Authenticator eingerichtet. Bitte mit Passwort und dem neuen Code anmelden."),
}


@router.get("/lang/{code}")
def set_language(code: str, request: Request, next: str = "/login"):
    """Umschalter „Deutsch · English“ auf Seiten ohne Anmeldung (Cookie, 1 Jahr)."""
    if not next.startswith("/") or next.startswith("//"):
        next = "/login"
    resp = RedirectResponse(next, status_code=303)
    if code in i18n.LANGS:
        set_cookie(resp, i18n.LANG_COOKIE, code, max_age=365 * 86400)
    return resp


@router.post("/login", dependencies=[Depends(csrf_protect_anon)])
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    totp: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    username = username.strip()
    if is_rate_limited(db, "login", ip, username):
        return render_anon(request, "login.html", status_code=429, username=username,
                           error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))

    parent = db.scalar(select(Parent).where(Parent.username == username))
    ok = False
    if parent is None:
        dummy_verify()
    elif verify_password(password, parent.password_hash):
        ok = verify_totp(db, parent, totp)

    if not ok:
        record_failure(db, "login", ip, username)
        return render_anon(request, "login.html", status_code=401, username=username,
                           error=_("Anmeldung fehlgeschlagen."))

    parent.detected_language = i18n.browser_language(request)   # für Mails/Push bei „Automatisch“
    db.commit()  # totp_last_step
    token = create_session(db, parent, request.headers.get("user-agent", ""))
    resp = RedirectResponse("/", status_code=303)
    set_cookie(resp, SESSION_COOKIE, token, max_age=settings.session_days * 86400)
    return resp


@router.post("/logout")
def logout(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    db.execute(delete(ParentSession).where(ParentSession.id == sess.id))
    db.commit()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---------- Passwort vergessen (E-Mail-Link) ----------

@router.get("/reset")
def reset_form(request: Request):
    return render_anon(request, "reset_request.html", mail_enabled=settings.mail_enabled)


@router.post("/reset", dependencies=[Depends(csrf_protect_anon)])
def reset_request(request: Request, ident: str = Form(""), db: Session = Depends(get_db)):
    ip = client_ip(request)
    if is_rate_limited(db, "reset", ip):
        return render_anon(request, "reset_request.html", status_code=429, mail_enabled=settings.mail_enabled,
                           error=_("Zu viele Anfragen. Bitte später erneut versuchen."))
    record_failure(db, "reset", ip)  # jede Anfrage zählt (Schutz vor Mail-Flut)
    if settings.mail_enabled:
        accounts.start_password_reset(db, ident[:255])
    # immer dieselbe Antwort: keine Auskunft, ob es den Account gibt
    return render_anon(request, "reset_request.html", mail_enabled=settings.mail_enabled, sent=True)


@router.get("/reset/{token}")
def reset_password_form(token: str, request: Request, db: Session = Depends(get_db)):
    if accounts.get_token(db, token, "password_reset") is None:
        return render_anon(request, "reset_password.html", status_code=404, invalid=True)
    return render_anon(request, "reset_password.html", token=token)


@router.post("/reset/{token}", dependencies=[Depends(csrf_protect_anon)])
def reset_password(token: str, request: Request, password: str = Form(""), repeat: str = Form(""),
                   db: Session = Depends(get_db)):
    t = accounts.get_token(db, token, "password_reset")
    if t is None:
        return render_anon(request, "reset_password.html", status_code=404, invalid=True)
    if err := accounts.password_error(password, repeat):
        return render_anon(request, "reset_password.html", status_code=400, token=token, error=err)
    parent = db.get(Parent, t.parent_id)
    parent.password_hash = hash_password(password)
    t.used_at = utcnow()
    accounts.end_sessions(db, parent.id)
    db.commit()
    accounts.notify(parent, N_("Passwort geändert"), N_("dein Passwort wurde über „Passwort vergessen“ neu gesetzt."))
    return RedirectResponse("/login?msg=reset", status_code=303)


# ---------- Authenticator einrichten (Link von einem anderen Elternteil) ----------

@router.get("/totp-setup/{token}")
def totp_setup_form(token: str, request: Request, db: Session = Depends(get_db)):
    t = accounts.get_token(db, token, "totp_setup")
    if t is None:
        return render_anon(request, "totp_setup.html", status_code=404, invalid=True)
    return _totp_setup_page(request, db, t, token)


def _totp_setup_page(request, db, t, token, status_code=200, error=None):
    parent = db.get(Parent, t.parent_id)
    uri = accounts.totp_uri_for(t.secret, parent.username)
    return render_anon(request, "totp_setup.html", status_code=status_code, token=token, username=parent.username,
                       qr_svg=totp_qr_svg(uri), secret=t.secret, error=error)


@router.post("/totp-setup/{token}", dependencies=[Depends(csrf_protect_anon)])
def totp_setup(token: str, request: Request, password: str = Form(""), totp: str = Form(""),
               db: Session = Depends(get_db)):
    t = accounts.get_token(db, token, "totp_setup")
    if t is None:
        return render_anon(request, "totp_setup.html", status_code=404, invalid=True)
    parent = db.get(Parent, t.parent_id)
    ip = client_ip(request)
    if is_rate_limited(db, "login", ip, parent.username):
        return _totp_setup_page(request, db, t, token, 429, _("Zu viele Fehlversuche. Bitte später erneut versuchen."))
    step = accounts.totp_step(t.secret, totp)
    if not verify_password(password, parent.password_hash) or step is None:
        record_failure(db, "login", ip, parent.username)
        return _totp_setup_page(request, db, t, token, 400, _("Passwort oder Code falsch."))
    parent.totp_secret, parent.totp_last_step = t.secret, step
    t.used_at = utcnow()
    accounts.end_sessions(db, parent.id)
    db.commit()
    accounts.notify(parent, N_("Authenticator eingerichtet"), N_("für deinen Account wurde ein neuer Authenticator eingerichtet."))
    return RedirectResponse("/login?msg=totp", status_code=303)
