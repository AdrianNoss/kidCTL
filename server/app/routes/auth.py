from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Parent, ParentSession
from ..security import (
    SESSION_COOKIE, client_ip, create_session, csrf_protect, csrf_protect_anon, dummy_verify,
    get_session, is_rate_limited, record_failure, set_cookie, verify_password, verify_totp,
)
from ..web import render_anon

router = APIRouter()


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_session(db, request):
        return RedirectResponse("/", status_code=303)
    return render_anon(request, "login.html")


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
                           error="Zu viele Fehlversuche. Bitte später erneut versuchen.")

    parent = db.scalar(select(Parent).where(Parent.username == username))
    ok = False
    if parent is None:
        dummy_verify()
    elif verify_password(password, parent.password_hash):
        ok = verify_totp(db, parent, totp)

    if not ok:
        record_failure(db, "login", ip, username)
        return render_anon(request, "login.html", status_code=401, username=username,
                           error="Anmeldung fehlgeschlagen.")

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
