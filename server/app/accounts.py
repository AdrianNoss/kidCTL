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
from . import crypto
from .models import Parent, ParentSession, ParentToken, utcnow
from .tenant import load_parent, platform
from .security import new_token, new_totp_secret, token_hash

RESET_MINUTES = 30
TOTP_SETUP_HOURS = 24
TOTP_CHANGE_MINUTES = 15
PURPOSES = {"password_reset", "totp_setup", "totp_change", "email_verify"}
EMAIL_VERIFY_HOURS = 48
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
    with platform(db):   # Benutzernamen sind über alle Familien eindeutig
        other = db.scalar(select(Parent.id).where(func.lower(Parent.username) == username.lower()))
    if other is not None and other != parent_id:
        return _("Benutzername existiert bereits.")
    return None


def email_error(email: str, db: Session | None = None, parent_id: int | None = None) -> str | None:
    """Format; im SaaS Pflicht; mit ``db`` auch Eindeutigkeit über alle Familien (ohne Groß/klein)."""
    if not email:
        return _("Bitte eine E-Mail-Adresse angeben.") if settings.saas else None
    if not EMAIL_RE.match(email):
        return _("Ungültige E-Mail-Adresse.")
    if db is not None:
        with platform(db):
            other = db.scalar(select(Parent.id).where(Parent.email_index == crypto.email_index(email)))
        if other is not None and other != parent_id:
            return _("Diese E-Mail-Adresse wird schon verwendet.")
    return None


def username_from_email(db: Session, email: str) -> str:
    """SaaS: Anmeldung per E-Mail; der (intern weiter eindeutige) Benutzername wird daraus abgeleitet."""
    base = re.sub(r"[^A-Za-z0-9._-]", "", email.split("@")[0])[:56] or "eltern"
    name, n = base, 1
    with platform(db):
        while db.scalar(select(Parent.id).where(func.lower(Parent.username) == name.lower())) is not None:
            n += 1
            name = f"{base}{n}"
    return name


def email_usable(parent: Parent) -> bool:
    """An diese Adresse darf gesendet werden: bestätigt (ohne SMTP lässt sich nichts bestätigen)."""
    return bool(parent.email) and (parent.email_verified_at is not None or not settings.mail_enabled)


def set_email(db: Session, parent: Parent, email: str) -> None:
    """Neue Adresse speichern; mit SMTP erst nach Klick auf den Bestätigungslink nutzbar."""
    if email == parent.email:
        return
    parent.email = email[:255]
    parent.email_verified_at = None if (email and settings.mail_enabled) else (utcnow() if email else None)
    if email and settings.mail_enabled:
        send_email_verification(db, parent)


def send_email_verification(db: Session, parent: Parent) -> bool:
    raw = create_token(db, parent, "email_verify", timedelta(hours=EMAIL_VERIFY_HOURS))
    db.commit()
    link = f"{settings.base_url}/email/verify/{raw}"
    with i18n.use(i18n.parent_language(parent)):
        return mail.send(parent.email, _("KidCTL: E-Mail-Adresse bestätigen"),
                         _("Hallo {name},").format(name=parent.display_name or parent.username) + "\n\n"
                         + _("bitte bestätige diese Adresse für deinen KidCTL-Account. Der Link gilt {hours} Stunden:").format(
                             hours=EMAIL_VERIFY_HOURS) + f"\n\n{link}\n\n"
                         + _("Danach kannst du dich mit ihr anmelden und bekommst Hinweise und Berichte.") + "\n"
                         + _("Falls du das nicht warst, kannst du diese Mail ignorieren.") + "\n")


def confirm_email(db: Session, raw: str) -> Parent | None:
    t = get_token(db, raw, "email_verify")
    if t is None:
        return None
    parent = load_parent(db, t.parent_id)
    t.used_at = utcnow()
    parent.email_verified_at = utcnow()
    db.commit()
    return parent


def find_by_login(db: Session, ident: str) -> Parent | None:
    """Benutzername oder E-Mail (ohne Groß/klein) -> Account; bei mehrdeutiger E-Mail keiner."""
    ident = ident.strip()
    if not ident:
        return None
    with platform(db):   # nur unverschlüsselte Spalten: Suchwert, Bestätigung, Benutzername
        pid = None
        if "@" in ident:   # E-Mail ist systemweit eindeutig
            row = db.execute(select(Parent.id, Parent.email_verified_at).where(
                Parent.email_index == crypto.email_index(ident), Parent.email_index != "")).first()
            if row is not None and (row.email_verified_at is not None or not settings.mail_enabled):
                pid = row.id   # erst nach Bestätigung zum Anmelden
        if pid is None:
            pid = db.scalar(select(Parent.id).where(Parent.username == ident))   # bisherige Accounts ohne E-Mail
    return load_parent(db, pid) if pid is not None else None


def check_password(db: Session, request, parent: Parent, password: str) -> str | None:
    """Aktuelles Passwort prüfen (mit Rate-Limiting wie beim Login); Fehlertext oder ``None``."""
    from .security import client_ip, is_rate_limited, record_failure, verify_password
    ip = client_ip(request)
    if is_rate_limited(db, "login", ip, parent.username):
        return _("Zu viele Fehlversuche. Bitte später erneut versuchen.")
    if not verify_password(password, parent.password_hash):
        record_failure(db, "login", ip, parent.username)
        return _("Aktuelles Passwort ist falsch.")
    return None


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
    if parent is None or not email_usable(parent):
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
    if email_usable(parent):
        with i18n.use(i18n.parent_language(parent)):
            mail.send(parent.email, f"KidCTL: {_(subject)}",
                      _("Hallo {name},").format(name=parent.display_name or parent.username) + f"\n\n{_(text).format(**fmt)}\n\n"
                      + _("Falls du das nicht warst, melde dich sofort an, ändere dein Passwort und prüfe die "
                          "Eltern-Accounts.") + "\n")


def new_secret() -> str:
    return new_totp_secret()
