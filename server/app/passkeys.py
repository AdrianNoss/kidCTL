"""Passkeys (WebAuthn) als vollständiger Login für Eltern und Betreiber, dazu die frische Bestätigung.

- Auffindbare Schlüssel (``residentKey=required``), ``userVerification=required`` (Biometrie/Geräte-PIN als zweiter
  Faktor), ``attestation=none``, ES256/RS256.
- RP-ID aus ``WEBAUTHN_RP_ID`` (Standard: Host aus ``BASE_URL``), Origin = ``BASE_URL`` – nie aus dem Request.
- Challenges serverseitig (Tabelle ``webauthn_challenge``, nur der Hash des Tokens im Cookie), einmalig, 5 Minuten.
- Sign-Count: Rückgang bei nicht synchronisierten Schlüsseln → abgelehnt und protokolliert.

Frische Bestätigung (Passwort ändern, Authenticator ändern, Passkeys hinzufügen/löschen): Passwort + Code **oder**
ein Passkey dieses Kontos, nicht älter als 5 Minuten (``confirmed_at`` an der Sitzung).
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes, generate_authentication_options, generate_registration_options, options_to_json,
    verify_authentication_response, verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference, AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
    ResidentKeyRequirement, UserVerificationRequirement,
)

from .config import settings
from .models import Passkey, WebAuthnChallenge, utcnow
from .security import new_token, token_hash

log = logging.getLogger("kidctl.passkeys")

TTL = timedelta(minutes=5)
FRESH = timedelta(minutes=5)
ALGS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]
COOKIES = {"parent": ("kidctl_webauthn", "/"), "operator": ("kidctl_admin_webauthn", "/admin")}
MAX_NAME = 64
MAX_CREDENTIAL = 64 * 1024


class PasskeyError(Exception):
    """Prüfung fehlgeschlagen – Text nur fürs Log, Nutzer sehen eine neutrale Meldung."""


def rp_id() -> str:
    return settings.webauthn_rp_id or (urlsplit(settings.base_url).hostname or "localhost")


def origin() -> str:
    return settings.base_url


# ---------- Challenges ----------

def _new_challenge(db: Session, purpose: str, kind: str, account_id: int | None, challenge: bytes) -> str:
    db.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at < utcnow()))   # Aufräumen
    token = new_token()
    db.add(WebAuthnChallenge(token_hash=token_hash(token), purpose=purpose, kind=kind, account_id=account_id,
                             challenge=challenge, expires_at=utcnow() + TTL))
    db.commit()
    return token


def _take_challenge(db: Session, token: str, purpose: str, kind: str) -> WebAuthnChallenge:
    """Challenge einlösen (einmalig): danach gelöscht, auch wenn die Prüfung scheitert."""
    if not token:
        raise PasskeyError("keine Challenge")
    c = db.scalar(select(WebAuthnChallenge).where(WebAuthnChallenge.token_hash == token_hash(token)))
    if c is None:
        raise PasskeyError("Challenge unbekannt oder schon verwendet")
    db.delete(c)
    db.commit()
    if c.purpose != purpose or c.kind != kind or c.expires_at < utcnow():
        raise PasskeyError("Challenge abgelaufen oder für einen anderen Zweck")
    return c


def set_cookie(response, kind: str, token: str) -> None:
    name, path = COOKIES[kind]
    response.set_cookie(name, token, max_age=int(TTL.total_seconds()), httponly=True, secure=settings.cookie_secure,
                        samesite="strict", path=path)


def cookie(request, kind: str) -> str:
    return request.cookies.get(COOKIES[kind][0], "")


def _credential(raw: str) -> dict:
    if not raw or len(raw) > MAX_CREDENTIAL:
        raise PasskeyError("Antwort fehlt oder ist zu groß")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise PasskeyError("Antwort ist kein JSON") from e
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise PasskeyError("Antwort unvollständig")
    return data


# ---------- Registrierung (im Profil, nach frischer Bestätigung) ----------

def registration_options(db: Session, kind: str, account_id: int, user_name: str, display_name: str) -> tuple[str, str]:
    """(Optionen als JSON für den Browser, Challenge-Token fürs Cookie)."""
    from . import branding
    existing = db.scalars(select(Passkey.credential_id).where(Passkey.kind == kind,
                                                              Passkey.account_id == account_id)).all()
    opts = generate_registration_options(
        rp_id=rp_id(), rp_name=branding.current().name, user_name=user_name[:64],
        user_display_name=(display_name or user_name)[:64], user_id=f"{kind}:{account_id}".encode(),
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in existing],
        supported_pub_key_algs=ALGS, timeout=int(TTL.total_seconds() * 1000))
    token = _new_challenge(db, "register", kind, account_id, opts.challenge)
    return options_to_json(opts), token


def register(db: Session, token: str, raw_credential: str, kind: str, account_id: int, name: str) -> Passkey:
    c = _take_challenge(db, token, "register", kind)
    if c.account_id != account_id:
        raise PasskeyError("Challenge gehört zu einem anderen Konto")
    data = _credential(raw_credential)
    try:
        v = verify_registration_response(credential=data, expected_challenge=c.challenge, expected_rp_id=rp_id(),
                                         expected_origin=origin(), require_user_verification=True,
                                         supported_pub_key_algs=ALGS)
    except Exception as e:   # py_webauthn wirft verschiedene Fehlerklassen
        raise PasskeyError(f"Registrierung ungültig: {e}") from e
    cred_id = bytes_to_base64url(v.credential_id)
    if db.scalar(select(Passkey.id).where(Passkey.credential_id == cred_id)) is not None:
        raise PasskeyError("Passkey schon registriert")
    transports = ",".join(t for t in (data.get("response", {}).get("transports") or []) if isinstance(t, str))[:128]
    pk = Passkey(kind=kind, account_id=account_id, credential_id=cred_id, public_key=v.credential_public_key,
                 sign_count=v.sign_count, transports=transports, backed_up=bool(v.credential_backed_up),
                 name=(" ".join(name.split())[:MAX_NAME] or "Passkey"))
    db.add(pk)
    db.commit()
    return pk


# ---------- Anmeldung und Bestätigung ----------

def authentication_options(db: Session, kind: str, purpose: str = "login", account_id: int | None = None) -> tuple[str, str]:
    """Anmeldung: ohne Liste (auffindbare Schlüssel, Browser schlägt vor). Bestätigung: nur Schlüssel des Kontos."""
    allow = None
    if account_id is not None:
        allow = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in db.scalars(
            select(Passkey.credential_id).where(Passkey.kind == kind, Passkey.account_id == account_id)).all()]
    opts = generate_authentication_options(rp_id=rp_id(), allow_credentials=allow,
                                           user_verification=UserVerificationRequirement.REQUIRED,
                                           timeout=int(TTL.total_seconds() * 1000))
    token = _new_challenge(db, purpose, kind, account_id, opts.challenge)
    return options_to_json(opts), token


def authenticate(db: Session, token: str, raw_credential: str, kind: str, purpose: str = "login",
                 account_id: int | None = None) -> Passkey:
    """Prüft die Antwort des Browsers; Ergebnis: der Passkey (Konto = ``kind``/``account_id`` des Passkeys)."""
    c = _take_challenge(db, token, purpose, kind)
    data = _credential(raw_credential)
    pk = db.scalar(select(Passkey).where(Passkey.credential_id == data["id"], Passkey.kind == kind))
    if pk is None:
        raise PasskeyError("Passkey unbekannt")
    if account_id is not None and pk.account_id != account_id:
        raise PasskeyError("Passkey gehört zu einem anderen Konto")
    try:
        v = verify_authentication_response(credential=data, expected_challenge=c.challenge, expected_rp_id=rp_id(),
                                           expected_origin=origin(), credential_public_key=pk.public_key,
                                           credential_current_sign_count=0, require_user_verification=True)
    except Exception as e:
        raise PasskeyError(f"Anmeldung ungültig: {e}") from e
    # Zähler: synchronisierte Passkeys liefern oft 0 – dann keine Prüfung; sonst muss er steigen (Klon-Erkennung)
    if not pk.backed_up and (pk.sign_count or v.new_sign_count) and v.new_sign_count <= pk.sign_count:
        log.warning("Passkey %s (%s %s): Zähler %s nach %s – abgelehnt (möglicher Klon)", pk.id, pk.kind,
                    pk.account_id, v.new_sign_count, pk.sign_count)
        raise PasskeyError("Zähler zurückgegangen")
    pk.sign_count = v.new_sign_count
    pk.backed_up = bool(v.credential_backed_up)
    pk.last_used_at = utcnow()
    db.commit()
    return pk


# ---------- Verwaltung ----------

def list_for(db: Session, kind: str, account_id: int) -> list[Passkey]:
    return list(db.scalars(select(Passkey).where(Passkey.kind == kind, Passkey.account_id == account_id)
                           .order_by(Passkey.created_at)).all())


def get_own(db: Session, kind: str, account_id: int, passkey_id: int) -> Passkey | None:
    pk = db.get(Passkey, passkey_id)
    return pk if pk is not None and pk.kind == kind and pk.account_id == account_id else None


def remove_all(db: Session, kind: str, account_id: int) -> int:
    """Wiederherstellung/Entzug (Passwort vergessen, Authenticator-Reset): alle Passkeys des Kontos löschen."""
    n = db.execute(delete(Passkey).where(Passkey.kind == kind, Passkey.account_id == account_id)).rowcount or 0
    return n


# ---------- Frische Bestätigung ----------

def is_fresh(sess) -> bool:
    return sess.confirmed_at is not None and utcnow() - sess.confirmed_at <= FRESH


def mark_fresh(db: Session, sess) -> None:
    sess.confirmed_at = utcnow()
    db.commit()


def confirm(db: Session, request, sess, account, password: str, code: str, totp_key: str) -> str | None:
    """Frische Bestätigung per Passwort + Authenticator-Code (sonst schon frisch: nichts zu tun). Fehlertext oder
    ``None``. Rate-Limiting wie beim Login; falsche Codes zählen für die Authenticator-Sperre des Kontos."""
    from .i18n import _
    from .security import client_ip, is_rate_limited, record_failure, totp_locked, verify_password, verify_totp
    if is_fresh(sess):
        return None
    ip = client_ip(request)
    if is_rate_limited(db, "login", ip, totp_key) or totp_locked(db, totp_key):
        return _("Zu viele Fehlversuche. Bitte später erneut versuchen.")
    if not password or not verify_password(password, account.password_hash):
        record_failure(db, "login", ip, totp_key)
        return _("Bestätigung fehlgeschlagen: Passwort oder Code falsch.")
    if not verify_totp(db, account, code):
        record_failure(db, "totp", ip, totp_key)
        return _("Bestätigung fehlgeschlagen: Passwort oder Code falsch.")
    mark_fresh(db, sess)
    return None


# ---------- Hinweis-Mails ----------

def notify_parent(parent, event: str, passkey: str = "") -> None:
    """Hinweis an das Elternteil (Passkey hinzugefügt/entfernt, bei Wiederherstellung alle entfernt)."""
    from . import accounts, i18n, mailer
    if accounts.email_usable(parent):
        mailer.send(parent.email, "passkey_notice", i18n.parent_language(parent),
                    name=parent.display_name or parent.username, event=event, passkey=passkey, admin=False)


def notify_operator(op, event: str, passkey: str = "") -> None:
    from . import mailer
    if op.email:
        mailer.send(op.email, "passkey_notice", "de", name=op.username, event=event, passkey=passkey, admin=True)
