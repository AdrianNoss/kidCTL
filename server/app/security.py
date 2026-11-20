"""Passwörter, TOTP, Tokens, Sessions, Rate-Limiting, CSRF."""

from __future__ import annotations

import hashlib
import hmac
import io
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import LoginAttempt, Parent, ParentSession, utcnow

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE = "kidctl_session"
CSRF_COOKIE = "kidctl_csrf"  # nur für Formulare vor dem Login (Double-Submit)
CSRF_FIELD = "csrf_token"

# Ohne 0/O/1/I/L – gut abtippbar
PAIRING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


# ---------- Hashing & Tokens ----------

def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)


# Gegen Timing-Unterschiede bei unbekanntem Benutzernamen
_DUMMY_HASH = pwd_context.hash(secrets.token_hex(8))


def dummy_verify() -> None:
    pwd_context.verify("x", _DUMMY_HASH)


def token_hash(token: str) -> str:
    """Hash für hochentropische Tokens (Session, Gerät, Kopplungscode)."""
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))


def normalize_pairing_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


# ---------- TOTP ----------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(parent: Parent) -> str:
    from . import branding
    return pyotp.TOTP(parent.totp_secret).provisioning_uri(
        name=parent.username, issuer_name=branding.totp_issuer())


def totp_qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def verify_totp(db: Session, parent: Parent, code: str) -> bool:
    """Prüft TOTP (±1 Zeitschritt) und verhindert Wiederverwendung desselben Codes."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False
    totp = pyotp.TOTP(parent.totp_secret)
    now_step = totp.timecode(datetime.now(timezone.utc))
    for step in (now_step - 1, now_step, now_step + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            if step <= parent.totp_last_step:
                return False
            parent.totp_last_step = step
            return True
    return False


# ---------- Rate-Limiting ----------

def is_rate_limited(db: Session, kind: str, ip: str, username: str = "") -> bool:
    since = utcnow() - timedelta(minutes=settings.login_window_minutes)
    base = select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.kind == kind, LoginAttempt.at >= since)
    if db.scalar(base.where(LoginAttempt.ip == ip)) >= settings.login_max_failures:
        return True
    if username and db.scalar(base.where(LoginAttempt.username == username)) >= settings.login_user_max_failures:
        return True
    return False


def totp_locked(db: Session, username: str) -> bool:
    """Zu viele falsche Codes bei richtigem Passwort – streng je Benutzer. Nur wer das Passwort kennt, kann das
    auslösen; ohne Passwort lässt sich so niemand aussperren."""
    since = utcnow() - timedelta(minutes=settings.login_window_minutes)
    return db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.kind == "totp", LoginAttempt.at >= since,
        LoginAttempt.username == username)) >= settings.login_max_failures


def begin_totp_attempt(db: Session, ip: str, username: str) -> LoginAttempt | None:
    """Code-Versuch vorab als Fehlversuch verbuchen (Commit, dann zählen) – so prüfen auch gleichzeitige Anfragen
    zusammen höchstens ``login_max_failures`` Codes. ``None`` = gesperrt. Bei richtigem Code ``totp_attempt_ok``."""
    a = LoginAttempt(kind="totp", ip=ip, username=username[:64])
    db.add(a)
    db.commit()
    since = utcnow() - timedelta(minutes=settings.login_window_minutes)
    n = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.kind == "totp", LoginAttempt.at >= since, LoginAttempt.username == username[:64]))
    if n > settings.login_max_failures:
        db.delete(a)
        db.commit()
        return None
    return a


def totp_attempt_ok(db: Session, attempt: LoginAttempt) -> None:
    """Richtiger Code: der vorab verbuchte Versuch zählt nicht als Fehlversuch."""
    db.execute(delete(LoginAttempt).where(LoginAttempt.id == attempt.id))
    db.commit()


def record_failure(db: Session, kind: str, ip: str, username: str = "") -> None:
    db.add(LoginAttempt(kind=kind, ip=ip, username=username[:64]))
    # Alte Einträge aufräumen
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < utcnow() - timedelta(days=1)))
    db.commit()


def client_ip(request: Request) -> str:
    # Traefik hängt die echte Gegenstelle rechts an; alles links davon kann der Client selbst mitschicken
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    if last := forwarded.rsplit(",", 1)[-1].strip():
        return last[:64]
    return request.client.host if request.client else "unknown"


# ---------- Sessions ----------

def create_session(db: Session, parent: Parent, user_agent: str) -> str:
    token = new_token()
    db.add(ParentSession(
        parent_id=parent.id,
        token_hash=token_hash(token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(days=settings.session_days),
        user_agent=user_agent[:255],
    ))
    db.execute(delete(ParentSession).where(ParentSession.expires_at < utcnow()))
    db.commit()
    return token


def get_session(db: Session, request: Request) -> ParentSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = db.scalar(select(ParentSession).where(ParentSession.token_hash == token_hash(token)))
    if sess is None or sess.expires_at < utcnow():
        return None
    return sess


def set_cookie(response, name: str, value: str, max_age: int, samesite: str = "lax") -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=settings.cookie_secure, samesite=samesite, path="/")


class LoginRequired(Exception):
    """Wird in main.py in eine Weiterleitung zum Login übersetzt."""


def require_session(request: Request, db: Session = Depends(get_db)) -> ParentSession:
    """Angemeldete Eltern: Sitzung prüfen und die Datenbanksitzung auf ihre Familie festlegen."""
    from .tenant import family_of_parent, platform, set_family
    with platform(db):
        sess = get_session(db, request)
        family_id = family_of_parent(db, sess.parent_id) if sess else None
        view = None
        if sess is None:
            from . import support
            view = support.view_for(db, request)        # Support-Ansicht des Betreibers (nur lesend)
    if view is not None:
        _billing_gate(db, request, view.family_id)
        support.enter(db, request, view)
        request.state.session = request.state.support = view
        request.state.family_id = view.family_id
        return view
    if sess is None or family_id is None:
        raise LoginRequired()
    _billing_gate(db, request, family_id)
    set_family(db, family_id)
    request.state.session = sess
    request.state.family_id = family_id
    return sess


class FamilyPaused(Exception):
    """Ruhemodus (Abo abgelaufen): main.py leitet zu den Einstellungen (Abo, Export, Löschen) um."""


PAUSED_ALLOWED = ("/settings", "/profile", "/logout", "/push/", "/support")   # Abo, Export, Löschen; Push für Hinweise


def _billing_gate(db: Session, request: Request, family_id: int) -> None:
    """SaaS: Hinweis-Banner zum Abo setzen; im Ruhemodus nur Einstellungen, Profil und Abmelden."""
    if not settings.saas:
        return
    from . import billing
    from .models import Family
    from .tenant import platform
    from .models import BillingAddress
    from sqlalchemy import select as _select
    with platform(db):
        family = db.get(Family, family_id)
        address = db.scalar(_select(BillingAddress).where(BillingAddress.family_id == family_id))
    if family is None:
        return
    request.state.billing = billing.banner(family, address)
    if family.paused_at is not None and not request.url.path.startswith(PAUSED_ALLOWED):
        raise FamilyPaused()


# ---------- CSRF ----------

CSRF_HEADER = "x-csrf-token"  # für fetch()-Aufrufe mit JSON-Body


async def _form_token(request: Request) -> str:
    if header := request.headers.get(CSRF_HEADER):
        return header
    if not request.headers.get("content-type", "").startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")):
        return ""
    form = await request.form()
    return str(form.get(CSRF_FIELD, ""))


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != settings.base_url:
        raise HTTPException(403, "Ungültige Herkunft")


async def csrf_protect(request: Request, sess: ParentSession = Depends(require_session)) -> ParentSession:
    """Für alle POST-Aktionen eingeloggter Eltern: Token muss zur Session passen."""
    from .support import deny_write
    deny_write(sess)
    _check_origin(request)
    if not hmac.compare_digest(await _form_token(request), sess.csrf_token):
        raise HTTPException(403, "CSRF-Token ungültig")
    return sess


async def csrf_protect_anon(request: Request) -> None:
    """Für Formulare vor dem Login: Double-Submit gegen SameSite=Strict-Cookie."""
    _check_origin(request)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not hmac.compare_digest(await _form_token(request), cookie):
        raise HTTPException(403, "CSRF-Token ungültig")
