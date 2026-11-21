"""Verwaltungsbereich: eigenes Betreiber-Profil (Passwort, Authenticator, Passkeys, Sitzungen)."""

from __future__ import annotations

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit, passkeys
from ..config import settings
from ..db import get_platform_db
from ..i18n import N_, _
from ..models import Operator, OperatorSession, utcnow
from . import profile_shared as ps
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Eigenes Profil ----------

TOTP_CHANGE_MINUTES = 15
PROFILE_MESSAGES = {"1": N_("Gespeichert."), "password": N_("Passwort geändert. Andere Sitzungen wurden abgemeldet."),
                    "totp": N_("Neuer Authenticator eingerichtet. Andere Sitzungen wurden abgemeldet."),
                    "sessions": N_("Alle anderen Sitzungen wurden abgemeldet."),
                    "confirmed": N_("Bestätigt. Für 5 Minuten kannst du Passwort, Authenticator und Passkeys ändern."),
                    "passkey_added": N_("Passkey angelegt."), "passkey_removed": N_("Passkey entfernt."),
                    "passkey_renamed": N_("Passkey umbenannt.")}


def _profile(request: Request, sess: OperatorSession, db: Session, status_code: int = 200, **ctx):
    others = db.scalar(select(func.count()).select_from(OperatorSession).where(
        OperatorSession.operator_id == sess.operator_id, OperatorSession.id != sess.id,
        OperatorSession.expires_at > utcnow()))
    msg = PROFILE_MESSAGES.get(request.query_params.get("saved", ""))
    return _render(request, "admin_profile.html", sess, status_code, roles=opr.ROLES, other_sessions=others,
                   message=_(msg) if msg else None, passkey_list=passkeys.list_for(db, "operator", sess.operator_id),
                   fresh=passkeys.is_fresh(sess), fresh_until=sess.confirmed_at and sess.confirmed_at + passkeys.FRESH,
                   passkey_base="/admin/profile", **ctx)


def _end_other_sessions(db: Session, sess: OperatorSession) -> None:
    db.execute(delete(OperatorSession).where(OperatorSession.operator_id == sess.operator_id,
                                             OperatorSession.id != sess.id))


def _notify_operator(op: Operator, subject: str, text: str) -> None:
    """Sicherheitshinweis an die hinterlegte Adresse des Betreibers (falls vorhanden)."""
    from .. import mailer
    if op.email:
        from ..i18n import use
        with use("de"):
            title, body = _(subject), _(text)
        mailer.send(op.email, "operator_notice", "de", name=op.username, title=title, text=body)


def _totp_token(op_id: int, secret: str, expires: int) -> str:
    """Signiert den vorbereiteten Schlüssel für das Bestätigungsformular (kein Serverzustand nötig)."""
    import hashlib
    import hmac
    sig = hmac.new(settings.secret_key.encode(), f"op-totp:{op_id}:{secret}:{expires}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{secret}.{expires}.{sig}"


def _totp_from_token(op_id: int, token: str) -> str | None:
    import hmac
    import time
    try:
        secret, expires, _sig = token.split(".")
        expires_at = int(expires)
    except ValueError:
        return None
    if expires_at < time.time() or not hmac.compare_digest(_totp_token(op_id, secret, expires_at), token):
        return None
    return secret


def _totp_page(request, sess, token: str, secret: str, status_code: int = 200, error: str | None = None):
    from .. import accounts
    from ..security import totp_qr_svg
    uri = accounts.totp_uri_for(secret, sess.operator.username)
    return _render(request, "admin_profile_totp.html", sess, status_code, token=token, secret=secret,
                   qr_svg=totp_qr_svg(uri), minutes=TOTP_CHANGE_MINUTES, error=error, fresh=passkeys.is_fresh(sess))


@router.get("/profile")
def profile(request: Request, sess: OperatorSession = Depends(opr.require_operator),
            db: Session = Depends(get_platform_db)):
    return _profile(request, sess, db)


@router.post("/profile")
def profile_save(request: Request, email: str = Form(""), ticket_mails: str = Form(""),
                 sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    from .. import accounts
    email = email.strip()[:255]
    if email and (err := accounts.email_error(email)):
        return _profile(request, sess, db, 400, error=err)
    if ticket_mails and not email:
        return _profile(request, sess, db, 400, error=_("Für Ticket-Mails bitte eine E-Mail-Adresse angeben."))
    op = db.get(Operator, sess.operator_id)
    old_email = op.email
    op.email, op.ticket_mails = email, bool(ticket_mails)
    if old_email != email:
        audit.record(db, audit.actor_of(sess), "operator", "email", old_email, email, object_id=op.id)
    db.commit()
    return RedirectResponse("/admin/profile?saved=1", status_code=303)


@router.post("/profile/password")
def profile_password(request: Request, current_password: str = Form(""), current_totp: str = Form(""),
                     password: str = Form(""), repeat: str = Form(""), sess: OperatorSession = Depends(opr.operator_csrf),
                     db: Session = Depends(get_platform_db)):
    from .. import accounts
    from ..security import hash_password
    op = db.get(Operator, sess.operator_id)
    error = accounts.password_error(password, repeat) or _op_confirm(db, request, sess, op, current_password, current_totp)
    if error:
        return _profile(request, sess, db, 400, card_error=error, card="password")
    op.password_hash = hash_password(password)
    _end_other_sessions(db, sess)
    audit.record(db, audit.actor_of(sess), "operator", "password", object_id=op.id)
    db.commit()
    _notify_operator(op, N_("Passwort geändert"), N_("das Passwort deines Betreiber-Zugangs wurde geändert."))
    return RedirectResponse("/admin/profile?saved=password", status_code=303)


@router.post("/profile/totp")
def profile_totp_start(request: Request, sess: OperatorSession = Depends(opr.operator_csrf)):
    """Neuen Authenticator vorbereiten – der alte gilt, bis der neue bestätigt ist."""
    import time
    from ..security import new_totp_secret
    secret = new_totp_secret()
    return _totp_page(request, sess, _totp_token(sess.operator_id, secret, int(time.time()) + TOTP_CHANGE_MINUTES * 60),
                      secret)


@router.post("/profile/totp/confirm")
def profile_totp_confirm(request: Request, token: str = Form(""), current_password: str = Form(""),
                         current_totp: str = Form(""), totp: str = Form(""),
                         sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    op = db.get(Operator, sess.operator_id)
    secret = _totp_from_token(op.id, token)
    if secret is None:
        return _profile(request, sess, db, 400, card_error=_("Die Einrichtung ist abgelaufen. Bitte neu starten."),
                        card="totp")
    error, step = ps.new_totp_step(_op_confirm(db, request, sess, op, current_password, current_totp), secret, totp)
    if error:
        return _totp_page(request, sess, token, secret, 400, error)
    op.totp_secret, op.totp_last_step = secret, step
    _end_other_sessions(db, sess)
    audit.record(db, audit.actor_of(sess), "operator", "totp", object_id=op.id)
    db.commit()
    _notify_operator(op, N_("Authenticator geändert"),
                     N_("für deinen Betreiber-Zugang wurde ein neuer Authenticator eingerichtet."))
    return RedirectResponse("/admin/profile?saved=totp", status_code=303)


def _op_confirm(db, request, sess, op, password: str, code: str) -> str | None:
    return passkeys.confirm(db, request, sess, op, password, code, f"op:{op.username}"[:64])


_json = ps.json_response


@router.post("/profile/confirm")
def profile_confirm(request: Request, current_password: str = Form(""), current_totp: str = Form(""),
                    sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    op = db.get(Operator, sess.operator_id)
    if error := _op_confirm(db, request, sess, op, current_password, current_totp):
        return _profile(request, sess, db, 400, card_error=error, card="passkeys")
    return RedirectResponse("/admin/profile?saved=confirmed#passkeys", status_code=303)


@router.post("/profile/confirm/passkey/options")
def profile_confirm_passkey_options(sess: OperatorSession = Depends(opr.operator_csrf),
                                    db: Session = Depends(get_platform_db)):
    return ps.confirm_passkey_options(db, "operator", sess.operator_id)


@router.post("/profile/confirm/passkey")
def profile_confirm_passkey(request: Request, credential: str = Form(""),
                            sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    return ps.confirm_passkey(db, request, sess, "operator", sess.operator_id, credential,
                              "/admin/profile?saved=confirmed#passkeys", " (Betreiber)")


@router.post("/profile/passkeys/options")
def profile_passkey_options(sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    if not passkeys.is_fresh(sess):
        return ps.not_fresh()
    op = db.get(Operator, sess.operator_id)
    return ps.registration_options(db, "operator", op.id, op.username, op.username)


@router.post("/profile/passkeys")
def profile_passkey_add(request: Request, credential: str = Form(""), name: str = Form(""),
                        sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    if not passkeys.is_fresh(sess):
        return ps.not_fresh()
    op = db.get(Operator, sess.operator_id)
    pk, error = ps.register_passkey(db, request, "operator", op.id, credential, name, " (Betreiber)")
    if error:
        return error
    audit.record(db, audit.actor_of(sess), "operator", "passkey", None, pk.name, object_id=op.id)
    db.commit()
    passkeys.notify_operator(op, "added", pk.name)
    return _json({"redirect": "/admin/profile?saved=passkey_added#passkeys"})


@router.post("/profile/passkeys/{passkey_id}/rename")
def profile_passkey_rename(passkey_id: int, name: str = Form(""), sess: OperatorSession = Depends(opr.operator_csrf),
                           db: Session = Depends(get_platform_db)):
    ps.rename_passkey(db, "operator", sess.operator_id, passkey_id, name)
    return RedirectResponse("/admin/profile?saved=passkey_renamed#passkeys", status_code=303)


@router.post("/profile/passkeys/{passkey_id}/delete")
def profile_passkey_delete(passkey_id: int, request: Request, current_password: str = Form(""),
                           current_totp: str = Form(""), sess: OperatorSession = Depends(opr.operator_csrf),
                           db: Session = Depends(get_platform_db)):
    op = db.get(Operator, sess.operator_id)
    pk = ps.own_passkey_or_404(db, "operator", op.id, passkey_id)
    if error := _op_confirm(db, request, sess, op, current_password, current_totp):
        return _profile(request, sess, db, 400, card_error=error, card="passkeys")
    name = pk.name
    db.delete(pk)
    audit.record(db, audit.actor_of(sess), "operator", "passkey", name, None, object_id=op.id)
    db.commit()
    passkeys.notify_operator(op, "removed", name)
    return RedirectResponse("/admin/profile?saved=passkey_removed#passkeys", status_code=303)


@router.post("/profile/sessions")
def profile_sessions(sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    """Alle anderen Sitzungen dieses Betreibers beenden (z. B. vergessener Rechner)."""
    _end_other_sessions(db, sess)
    audit.record(db, audit.actor_of(sess), "operator", "sessions", object_id=sess.operator_id)
    db.commit()
    return RedirectResponse("/admin/profile?saved=sessions", status_code=303)
