"""Registrierung neuer Familien (Etappe 4.3) – nur im SaaS mit REGISTRATION=open|invite."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pyotp
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import accounts, branding, i18n, operators, signup
from ..config import settings
from ..db import get_platform_db
from ..i18n import _
from ..security import (
    SESSION_COOKIE, client_ip, create_session, csrf_protect_anon, is_rate_limited, new_totp_secret, record_failure,
    set_cookie, totp_qr_svg, verify_totp,
)
from ..tenant import platform_session
from ..web import render_anon

router = APIRouter()
SECRET_RE = re.compile(r"^[A-Z2-7]{32}$")


def _open() -> None:
    if settings.registration == "closed":
        raise HTTPException(404)


@router.get("/signup", dependencies=[Depends(_open)])
def signup_form(request: Request):
    return render_anon(request, "signup.html", invite=settings.registration == "invite", form={})


def _send(email: str, code: str, lang: str) -> None:
    with platform_session() as db:
        signup.start(db, email, code, lang)


@router.post("/signup", dependencies=[Depends(_open), Depends(csrf_protect_anon)])
def signup_request(request: Request, background: BackgroundTasks, email: str = Form(""), code: str = Form(""),
                   db: Session = Depends(get_platform_db)):
    invite = settings.registration == "invite"
    email, ip = email.strip()[:255], client_ip(request)

    def page(status, error):
        return render_anon(request, "signup.html", status_code=status, invite=invite, error=error,
                           form={"email": email, "code": code})

    if is_rate_limited(db, "signup", ip):
        return page(429, _("Zu viele Anfragen. Bitte später erneut versuchen."))
    record_failure(db, "signup", ip)              # jede Anfrage zählt (Schutz vor Mail-Flut)
    if not settings.mail_enabled:
        return page(503, _("Die Registrierung braucht E-Mail-Versand, der auf diesem Server nicht eingerichtet ist."))
    if error := accounts.email_error(email):
        return page(400, error)
    if invite and operators.find_code(db, code) is None:
        return page(400, _("Der Einladungscode ist ungültig, abgelaufen oder schon eingelöst."))
    # nach der Antwort senden und immer gleich antworten: keine Auskunft, ob die Adresse schon einen Account hat
    background.add_task(_send, email, code, i18n.current())
    return render_anon(request, "signup.html", invite=invite, sent=email, form={})


def _confirm_page(request, token, email, secret, status_code=200, error=None, form=None):
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=branding.totp_issuer())
    return render_anon(request, "signup_confirm.html", status_code=status_code, token=token, email=email,
                       secret=secret, qr_svg=totp_qr_svg(uri), error=error, form=form or {})


@router.get("/signup/{token}", dependencies=[Depends(_open)])
def signup_confirm_form(token: str, request: Request, db: Session = Depends(get_platform_db)):
    data = signup.read_token(token)
    error = signup.link_error(db, *data) if data else _("Dieser Link ist ungültig oder abgelaufen.")
    if error:
        return render_anon(request, "signup_confirm.html", status_code=404, invalid=error)
    return _confirm_page(request, token, data[0], new_totp_secret())


@router.post("/signup/{token}", dependencies=[Depends(_open), Depends(csrf_protect_anon)])
def signup_confirm(token: str, request: Request, display_name: str = Form(""), password: str = Form(""),
                   repeat: str = Form(""), secret: str = Form(""), totp: str = Form(""),
                   db: Session = Depends(get_platform_db)):
    data = signup.read_token(token)
    error = signup.link_error(db, *data) if data else _("Dieser Link ist ungültig oder abgelaufen.")
    if error:
        return render_anon(request, "signup_confirm.html", status_code=404, invalid=error)
    email, code = data
    ip = client_ip(request)
    if not SECRET_RE.match(secret):
        secret = new_totp_secret()
    form = {"display_name": display_name}
    if is_rate_limited(db, "signup", ip):
        return _confirm_page(request, token, email, secret, 429, _("Zu viele Fehlversuche. Bitte später erneut versuchen."), form)
    if error := accounts.password_error(password, repeat):
        return _confirm_page(request, token, email, secret, 400, error, form)
    probe = SimpleNamespace(totp_secret=secret, totp_last_step=0)
    if not verify_totp(db, probe, totp):
        record_failure(db, "signup", ip)
        return _confirm_page(request, token, email, secret, 400,
                             _("Der Code passt nicht. Bitte die Uhrzeit des Handys prüfen und den aktuellen Code eingeben."), form)
    try:
        parent = signup.complete(db, email, code, display_name, password, secret, probe.totp_last_step)
    except signup.SignupError as e:
        return render_anon(request, "signup_confirm.html", status_code=409, invalid=str(e))
    except IntegrityError:                         # gleichzeitig mit derselben Adresse registriert
        db.rollback()
        return render_anon(request, "signup_confirm.html", status_code=409,
                           invalid=_("Für diese E-Mail-Adresse gibt es schon einen Account. Bitte anmelden oder „Passwort vergessen“ nutzen."))
    parent.detected_language = i18n.browser_language(request)
    db.commit()
    resp = RedirectResponse("/", status_code=303)
    set_cookie(resp, SESSION_COOKIE, create_session(db, parent, request.headers.get("user-agent", "")),
               max_age=settings.session_days * 86400)
    return resp
