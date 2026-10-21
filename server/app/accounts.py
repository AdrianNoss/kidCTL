"""Eltern-Accounts: Profil, Passwort, Authenticator (TOTP), Einmal-Links (Passwort vergessen, Einrichtung).

Sicherheitsregeln:
- „Passwort vergessen“ setzt nur das Passwort zurück; die Anmeldung braucht weiterhin den Authenticator.
- Ein verlorener Authenticator wird von einem anderen Elternteil zurückgesetzt (oder per CLI): Das alte
  Secret wird sofort ungültig, der Einrichtungslink verlangt zusätzlich das Passwort.
- Einmal-Links: nur der Hash wird gespeichert, kurze Gültigkeit, einmal verwendbar.
- Nach Passwort- oder Authenticator-Änderung werden andere Sitzungen beendet.
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta, timezone

import pyotp
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import i18n
from .i18n import _
from . import mail
from .config import settings
from .models import Parent, ParentSession, ParentToken, utcnow
from .security import new_token, new_totp_secret, token_hash

RESET_MINUTES = 30
TOTP_SETUP_HOURS = 24
TOTP_CHANGE_MINUTES = 15
PURPOSES = {"password_reset", "totp_setup", "totp_change"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


# ---------- Prüfungen ----------

def password_error(pw: str, repeat: str | None = None) -> str | None:
    if len(pw) < 12:
        return _("Passwort muss mindestens 12 Zeichen haben.")
    if len(pw.encode()) > 72:
        return _("Passwort darf höchstens 72 Bytes haben.")
    if repeat is not None and pw != repeat:
        return _("Die Passwörter stimmen nicht überein.")
    return None


def username_error(db: Session, username: str, parent_id: int | None = None) -> str | None:
    if not USERNAME_RE.match(username):
        return _("Benutzername: 1–64 Zeichen, nur Buchstaben, Ziffern und . _ @ -")
    other = db.scalar(select(Parent).where(func.lower(Parent.username) == username.lower()))
    if other is not None and other.id != parent_id:
        return _("Benutzername existiert bereits.")
    return None


def email_error(email: str) -> str | None:
    if email and not EMAIL_RE.match(email):
        return _("Ungültige E-Mail-Adresse.")
    return None


def find_by_login(db: Session, ident: str) -> Parent | None:
    """Benutzername oder E-Mail (ohne Groß/klein) -> Account; bei mehrdeutiger E-Mail keiner."""
    ident = ident.strip()
    if not ident:
        return None
    p = db.scalar(select(Parent).where(Parent.username == ident))
    if p is not None:
        return p
    matches = db.scalars(select(Parent).where(func.lower(Parent.email) == ident.lower(), Parent.email != "")).all()
    return matches[0] if len(matches) == 1 else None


# ---------- TOTP ----------

def totp_step(secret: str, code: str) -> int | None:
    """Zeitschritt, zu dem ``code`` passt (±1), sonst ``None``."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6) or not secret:
        return None
    totp = pyotp.TOTP(secret)
    now = totp.timecode(datetime.now(timezone.utc))
    for step in (now - 1, now, now + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def totp_uri_for(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=settings.totp_issuer)


# ---------- Einmal-Links ----------

def create_token(db: Session, parent: Parent, purpose: str, lifetime: timedelta, secret: str = "") -> str:
    assert purpose in PURPOSES
    # ältere offene Links desselben Zwecks verfallen
    db.execute(delete(ParentToken).where(ParentToken.parent_id == parent.id, ParentToken.purpose == purpose))
    raw = new_token()
    db.add(ParentToken(parent_id=parent.id, purpose=purpose, token_hash=token_hash(raw), secret=secret,
                       expires_at=utcnow() + lifetime))
    return raw


def get_token(db: Session, raw: str, purpose: str) -> ParentToken | None:
    if not raw or len(raw) > 128:
        return None
    t = db.scalar(select(ParentToken).where(ParentToken.token_hash == token_hash(raw),
                                            ParentToken.purpose == purpose))
    if t is None or t.used_at is not None or t.expires_at < utcnow():
        return None
    return t


def end_sessions(db: Session, parent_id: int, keep_session_id: int | None = None) -> None:
    q = delete(ParentSession).where(ParentSession.parent_id == parent_id)
    if keep_session_id is not None:
        q = q.where(ParentSession.id != keep_session_id)
    db.execute(q)


def prune_tokens(db: Session) -> None:
    db.execute(delete(ParentToken).where(ParentToken.expires_at < utcnow() - timedelta(days=1)))


# ---------- Abläufe ----------

def start_password_reset(db: Session, ident: str) -> bool:
    """Reset-Mail senden, falls Account mit E-Mail existiert. Rückgabe nur für Tests/Logs – der
    Aufrufer zeigt immer dieselbe Antwort (keine Auskunft, ob ein Account existiert)."""
    prune_tokens(db)
    parent = find_by_login(db, ident)
    if parent is None or not parent.email:
        db.commit()
        return False
    raw = create_token(db, parent, "password_reset", timedelta(minutes=RESET_MINUTES))
    db.commit()
    link = f"{settings.base_url}/reset/{raw}"
    with i18n.use(i18n.parent_language(parent)):   # Sprache des Accounts, nicht des anfragenden Browsers
        return mail.send(parent.email, _("KidCTL: Passwort zurücksetzen"),
                         _("Hallo {name},").format(name=parent.display_name or parent.username) + "\n\n"
                         + _("für deinen KidCTL-Account „{username}“ wurde ein neues Passwort angefordert.").format(
                             username=parent.username) + "\n"
                         + _("Über diesen Link kannst du es {minutes} Minuten lang einmal neu setzen:").format(
                             minutes=RESET_MINUTES) + f"\n\n{link}\n\n"
                         + _("Zum Anmelden brauchst du danach weiterhin deine Authenticator-App.") + "\n"
                         + _("Falls du das nicht warst, kannst du diese Mail ignorieren.") + "\n")


def notify(parent: Parent, subject: str, text: str, **fmt) -> None:
    """Sicherheitshinweis an die hinterlegte Adresse (falls vorhanden). ``subject``/``text`` sind mit ``N_``
    markierte Texte und werden in der Sprache des Elternteils gesendet."""
    if parent.email:
        with i18n.use(i18n.parent_language(parent)):
            mail.send(parent.email, f"KidCTL: {_(subject)}",
                      _("Hallo {name},").format(name=parent.display_name or parent.username) + f"\n\n{_(text).format(**fmt)}\n\n"
                      + _("Falls du das nicht warst, melde dich sofort an, ändere dein Passwort und prüfe die "
                          "Eltern-Accounts.") + "\n")


def new_secret() -> str:
    return new_totp_secret()
