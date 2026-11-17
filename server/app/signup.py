"""Registrierung neuer Familien im SaaS (Etappe 4.3, ``REGISTRATION=open|invite``).

Ablauf: E-Mail (+ Einladungscode) → Bestätigungslink per Mail → eine Seite mit Passwort und Authenticator → Familie und
Inhaber-Account werden zusammen angelegt, danach ist man angemeldet und legt das erste Kind an.

Der Link ist signiert und trägt E-Mail, Code und Ablaufzeit selbst – bis zur Bestätigung wird nichts gespeichert,
also auch keine unverschlüsselte E-Mail-Adresse. Einmalig wird er dadurch, dass die Adresse danach vergeben und der Code
eingelöst ist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import accounts, i18n, mail, operators
from .config import settings
from .i18n import _
from .models import ROLE_OWNER, Family, Membership, Parent, SignupCode, utcnow
from .security import hash_password, token_hash
from .tenant import set_family

LINK_HOURS = 24


class SignupError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(settings.secret_key.encode(), b"kidctl signup|" + payload.encode(), hashlib.sha256).digest())


def make_token(email: str, code: str = "", now: float | None = None) -> str:
    exp = int((now or time.time()) + LINK_HOURS * 3600)
    payload = _b64(json.dumps({"e": email, "c": operators.normalize_code(code), "x": exp}, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def read_token(raw: str) -> tuple[str, str] | None:
    """(E-Mail, Code) aus einem gültigen, nicht abgelaufenen Link – sonst ``None``."""
    payload, _sep, sig = (raw or "").partition(".")
    if not payload or len(raw) > 1024 or not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return None
    if int(data.get("x", 0)) < time.time():
        return None
    return str(data.get("e", "")), str(data.get("c", ""))


def link_error(db: Session, email: str, code: str) -> str | None:
    """Warum dieser Link (nicht mehr) zu einem Konto führt – vergebene Adresse, eingelöster Code, Registrierung zu."""
    if settings.registration == "closed":
        return _("Die Registrierung ist geschlossen.")
    if accounts.email_error(email, db):
        return _("Für diese E-Mail-Adresse gibt es schon einen Account. Bitte anmelden oder „Passwort vergessen“ nutzen.")
    if settings.registration == "invite" and operators.find_code(db, code) is None:
        return _("Der Einladungscode ist ungültig, abgelaufen oder schon eingelöst.")
    return None


def start(db: Session, email: str, code: str, lang: str) -> bool:
    """Bestätigungslink schicken – nur wenn die Adresse frei ist (der Aufrufer antwortet immer gleich)."""
    if link_error(db, email, code):
        return False
    link = f"{settings.base_url}/signup/{make_token(email, code)}"
    with i18n.use(lang):
        return mail.send(email, _("KidCTL: E-Mail-Adresse bestätigen"),
                         _("Willkommen bei KidCTL!") + "\n\n"
                         + _("Über diesen Link bestätigst du deine E-Mail-Adresse und legst Passwort und Authenticator "
                             "fest ({hours} Stunden gültig):").format(hours=LINK_HOURS) + f"\n\n{link}\n\n"
                         + _("Falls du dich nicht registriert hast, ignoriere diese Mail.") + "\n")


def complete(db: Session, email: str, code: str, display_name: str, password: str, totp_secret: str,
             totp_last_step: int) -> Parent:
    """Familie und Inhaber-Account anlegen (eine Transaktion); der Code wird dabei eingelöst."""
    code_id = None
    if settings.registration == "invite":
        code_id = db.execute(update(SignupCode).where(
            SignupCode.code_hash == token_hash(operators.normalize_code(code)), SignupCode.used_at.is_(None),
            SignupCode.expires_at > utcnow()).values(used_at=utcnow()).returning(SignupCode.id)).scalar()
        if code_id is None:
            db.rollback()
            raise SignupError(_("Der Einladungscode ist ungültig, abgelaufen oder schon eingelöst."))
    family = Family()
    db.add(family)
    db.flush()                                    # Schlüssel der Familie entsteht hier (tenant._family_key)
    set_family(db, family.id)
    parent = Parent(username=accounts.username_from_email(db, email), display_name=display_name.strip()[:128],
                    password_hash=hash_password(password), totp_secret=totp_secret, totp_last_step=totp_last_step,
                    email=email, email_verified_at=utcnow())
    db.add(parent)
    db.flush()
    db.add(Membership(parent_id=parent.id, role=ROLE_OWNER))
    if code_id is not None:
        db.execute(update(SignupCode).where(SignupCode.id == code_id).values(family_id=family.id))
    db.commit()
    return parent
