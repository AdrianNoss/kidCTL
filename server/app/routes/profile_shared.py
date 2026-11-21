"""Gemeinsame Bausteine der Profilseiten von Eltern (/profile) und Betreibern (/admin/profile).

Nur was in beiden Bereichen wirklich gleich abläuft: frische Bestätigung per Passkey, Passkeys anlegen,
umbenennen und auswählen sowie die Prüfung eines neuen Authenticator-Codes. Kontoart (`kind`: "parent" oder
"operator"), Protokoll, Hinweis-Mails und Redirect-Ziele bleiben bei den Routen.
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .. import accounts, passkeys
from ..i18n import _


def json_response(data: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers={"Cache-Control": "no-store"})


def _with_cookie(kind: str, options: dict, token: str) -> JSONResponse:
    resp = json_response({"options": options})
    passkeys.set_cookie(resp, kind, token)
    return resp


def confirm_passkey_options(db, kind: str, account_id: int) -> JSONResponse:
    options, token = passkeys.authentication_options(db, kind, "confirm", account_id)
    return _with_cookie(kind, options, token)


def confirm_passkey(db, request: Request, sess, kind: str, account_id: int, credential: str, redirect: str,
                    label: str = "") -> JSONResponse:
    """Frische Bestätigung mit einem Passkey des eigenen Kontos. `label` ergänzt die Log-Zeile (z. B. „ (Betreiber)“)."""
    try:
        passkeys.authenticate(db, passkeys.cookie(request, kind), credential, kind, "confirm", account_id)
    except passkeys.PasskeyError as e:
        passkeys.log.info("Bestätigung per Passkey%s abgelehnt: %s", label, e)
        return json_response({"error": _("Bestätigung mit Passkey fehlgeschlagen.")}, 400)
    passkeys.mark_fresh(db, sess)
    return json_response({"redirect": redirect})


def not_fresh() -> JSONResponse:
    return json_response({"error": _("Bitte zuerst bestätigen.")}, 403)


def registration_options(db, kind: str, account_id: int, user_name: str, display_name: str) -> JSONResponse:
    options, token = passkeys.registration_options(db, kind, account_id, user_name, display_name)
    return _with_cookie(kind, options, token)


def register_passkey(db, request: Request, kind: str, account_id: int, credential: str, name: str, label: str = ""):
    """Neuen Passkey anlegen. Liefert (Passkey, None) oder (None, Fehlerantwort)."""
    try:
        return passkeys.register(db, passkeys.cookie(request, kind), credential, kind, account_id, name), None
    except passkeys.PasskeyError as e:
        passkeys.log.info("Passkey-Registrierung%s abgelehnt: %s", label, e)
        return None, json_response({"error": _("Der Passkey ließ sich nicht anlegen.")}, 400)


def own_passkey_or_404(db, kind: str, account_id: int, passkey_id: int):
    pk = passkeys.get_own(db, kind, account_id, passkey_id)
    if pk is None:
        raise HTTPException(404)
    return pk


def rename_passkey(db, kind: str, account_id: int, passkey_id: int, name: str) -> None:
    pk = own_passkey_or_404(db, kind, account_id, passkey_id)
    pk.name = " ".join(name.split())[:passkeys.MAX_NAME] or pk.name
    db.commit()


def new_totp_step(confirm_error: str | None, secret: str, code: str) -> tuple[str | None, int | None]:
    """Code des neuen Authenticators prüfen (nach der Bestätigung). Liefert (Fehler, Zeitschritt)."""
    step = accounts.totp_step(secret, code)
    if not confirm_error and step is None:
        return _("Der Code passt nicht zum neuen QR-Code."), step
    return confirm_error, step
