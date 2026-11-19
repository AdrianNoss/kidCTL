"""Verwaltungsbereich: eigenes Profil – E-Mail, Passwort, Authenticator, andere Sitzungen (beide Rollen)."""

import pyotp
import pytest

from app import operators
from app.models import AdminLog, Operator, OperatorSession
from app.security import verify_password
from conftest import PASSWORD, csrf_from, platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)

NEW = "ganz-neues-passwort-42"


@pytest.fixture
def outbox(monkeypatch):
    from app import mail
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, **kw: sent.append({"to": to, "subject": subject}) or True)
    return sent


def _other_session(op_id):
    """Zweite Sitzung desselben Betreibers (anderes Gerät)."""
    with platform_db() as db:
        operators.create_session(db, db.get(Operator, op_id))
        db.commit()


def _sessions(op_id):
    with platform_db() as db:
        return db.query(OperatorSession).filter_by(operator_id=op_id).count()


def _log(field):
    with platform_db() as db:
        return [(e.actor, e.field) for e in db.query(AdminLog).filter_by(object="operator", field=field)]


@pytest.fixture(params=["admin", "support"])
def op(request, operator):  # noqa: F811
    with platform_db() as db:
        db.get(Operator, operator.id).role = request.param
        db.commit()
    return operator


def test_profile_page_and_email(client, op, outbox):
    admin_login(client, op)
    _other_session(op.id)
    page = client.get("/admin/profile").text
    assert "Passwort ändern" in page and "Neuen Authenticator einrichten" in page
    assert "an 1 weiteren Gerät angemeldet" in page
    tok = csrf_from(page)
    r = client.post("/admin/profile", data={"csrf_token": tok, "email": "op@example.org"}, follow_redirects=False)
    assert r.status_code == 303
    assert _log("email") == [("betrieb", "email")]
    assert client.post("/admin/profile", data={"csrf_token": tok, "email": "kaputt"}).status_code == 400


def test_password_change(client, op, outbox):
    admin_login(client, op)
    _other_session(op.id)
    tok = csrf_from(client.get("/admin/profile").text)
    data = {"csrf_token": tok, "current_password": PASSWORD, "password": NEW, "repeat": NEW}
    r = client.post("/admin/profile/password", data={**data, "current_password": "falsch"})
    assert r.status_code == 400 and "Aktuelles Passwort ist falsch" in r.text
    assert client.post("/admin/profile/password", data={**data, "repeat": "anders-anders-1"}).status_code == 400
    assert client.post("/admin/profile/password", data={**data, "password": "kurz", "repeat": "kurz"}).status_code == 400
    with platform_db() as db:
        db.get(Operator, op.id).email = "op@example.org"
        db.commit()
    r = client.post("/admin/profile/password", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/profile?saved=password"
    with platform_db() as db:
        assert verify_password(NEW, db.get(Operator, op.id).password_hash)
    assert _sessions(op.id) == 1                                   # andere Sitzung beendet, diese bleibt
    assert "Passwort geändert" in client.get("/admin/profile?saved=password").text
    assert _log("password") == [("betrieb", "password")]
    assert any("Passwort geändert" in m["subject"] for m in outbox)


def test_totp_change(client, op, outbox):
    admin_login(client, op)
    _other_session(op.id)
    tok = csrf_from(client.get("/admin/profile").text)
    page = client.post("/admin/profile/totp", data={"csrf_token": tok}).text
    secret = page.split("<code>")[1].split("</code>")[0]
    token = page.split('name="token" value="')[1].split('"')[0]
    with platform_db() as db:
        assert db.get(Operator, op.id).totp_secret != secret        # alter gilt bis zur Bestätigung
    base = {"csrf_token": tok, "token": token, "current_password": PASSWORD}
    assert client.post("/admin/profile/totp/confirm", data={**base, "totp": "000000"}).status_code == 400
    forged = token.replace(secret, "A" * len(secret))
    r = client.post("/admin/profile/totp/confirm", data={**base, "token": forged, "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 400 and "abgelaufen" in r.text           # Signatur schützt den Schlüssel
    r = client.post("/admin/profile/totp/confirm", data={**base, "totp": pyotp.TOTP(secret).now()},
                    follow_redirects=False)
    assert r.status_code == 303
    with platform_db() as db:
        assert db.get(Operator, op.id).totp_secret == secret
    assert _sessions(op.id) == 1
    assert _log("totp") == [("betrieb", "totp")]
    client.cookies.clear()                                           # Anmeldung mit dem neuen Authenticator
    with platform_db() as db:
        new_op = db.get(Operator, op.id)
    import time
    next_code = pyotp.TOTP(secret).at(time.time() + 30)              # gleicher Code zweimal wäre ein Replay
    assert admin_login(client, new_op, code=next_code).status_code == 303


def test_end_other_sessions(client, op):
    admin_login(client, op)
    _other_session(op.id)
    _other_session(op.id)
    tok = csrf_from(client.get("/admin/profile").text)
    r = client.post("/admin/profile/sessions", data={"csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 303 and _sessions(op.id) == 1
    assert "Du bist nur hier angemeldet" in client.get("/admin/profile").text
    assert client.post("/admin/profile/sessions", data={}).status_code in (400, 403)   # CSRF


def test_profile_requires_login(client):
    for path in ("/admin/profile/password", "/admin/profile/totp", "/admin/profile/sessions"):
        assert client.post(path, data={}, follow_redirects=False).status_code in (303, 401, 403)
