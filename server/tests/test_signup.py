"""Etappe 4.3: Registrierung neuer Familien (REGISTRATION=open|invite|closed)."""

import re
import time

import pyotp
import pytest
from sqlalchemy import select

from app import config, mail, signup, web
from app.config import Settings
from app.models import ROLE_OWNER, Family, Membership, Parent, SignupCode
from conftest import csrf_from, platform_db
from app.tenant import family_session

PW = "ein sicheres passwort"


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, **kw: sent.append((to, subject, body)) or True)
    return sent


def _mode(monkeypatch, registration):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setattr(Settings, "registration", property(lambda s: registration))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)
    monkeypatch.setitem(web.templates.env.globals, "REGISTRATION", registration)


def _post(client, url, data):
    page = client.get(url)
    return client.post(url, data={"csrf_token": csrf_from(page.text), **data}, follow_redirects=False)


def _link(outbox):
    return re.search(r"http://testserver(/signup/[^\s]+)", outbox[-1][2]).group(1)


def _confirm(client, link, password=PW, code=None):
    page = client.get(link)
    secret = re.search(r'name="secret" value="([A-Z2-7]+)"', page.text).group(1)
    r = client.post(link, data={"csrf_token": csrf_from(page.text), "secret": secret, "display_name": "Mama",
                                "password": password, "repeat": password,
                                "totp": code or pyotp.TOTP(secret).now()}, follow_redirects=False)
    return r, secret


def test_closed_by_default_in_single_mode(client, monkeypatch):
    monkeypatch.delenv("REGISTRATION", raising=False)
    assert config._registration("single") == "closed" and config._registration("saas") == "invite"
    monkeypatch.setenv("REGISTRATION", "open")
    assert config._registration("saas") == "open" and config._registration("single") == "closed"
    monkeypatch.setenv("REGISTRATION", "jeder")
    with pytest.raises(RuntimeError):
        config._registration("saas")
    assert client.get("/signup").status_code == 404
    assert "/signup" not in client.get("/login").text


def test_open_signup_creates_family_and_owner(client, monkeypatch, outbox):
    _mode(monkeypatch, "open")
    assert "/signup" in client.get("/login").text
    assert _post(client, "/signup", {"email": "kaputt"}).status_code == 400
    r = _post(client, "/signup", {"email": "neu@example.org"})
    assert r.status_code == 200 and "neu@example.org" in r.text and outbox[-1][0] == "neu@example.org"
    link = _link(outbox)
    with platform_db() as db:
        assert db.scalar(select(Family.id).order_by(Family.id.desc())) == 1   # noch nichts angelegt
    r, secret = _confirm(client, link, code="000000")
    assert r.status_code == 400 and "Code passt nicht" in r.text
    r, secret = _confirm(client, link)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "Kind anlegen" in client.get("/").text                             # angemeldet, eigene leere Familie
    with platform_db() as db:
        pid = db.scalar(select(Parent.id).order_by(Parent.id.desc()))
        fid = db.scalar(select(Membership.family_id).where(Membership.parent_id == pid))
        assert fid != 1 and db.scalar(select(Membership.role).where(Membership.parent_id == pid)) == ROLE_OWNER
    with family_session(fid) as db:
        p = db.get(Parent, pid)
        assert p.email == "neu@example.org" and p.email_verified_at and p.totp_secret == secret and p.display_name == "Mama"
    client.cookies.clear()
    assert "schon einen Account" in client.get(link).text                     # Link nur einmal nutzbar
    n = len(outbox)
    assert _post(client, "/signup", {"email": "NEU@example.org"}).status_code == 200
    assert len(outbox) == n                                                   # vergeben: gleiche Antwort, keine Mail


def test_invite_signup_requires_and_redeems_code(client, monkeypatch, outbox):
    from app import operators
    _mode(monkeypatch, "invite")
    with platform_db() as db:
        code = operators.create_code(db, "Familie Test", None)
    assert _post(client, "/signup", {"email": "a@example.org", "code": "AAAA-BBBB-CCCC"}).status_code == 400
    assert not outbox
    assert _post(client, "/signup", {"email": "a@example.org", "code": code.lower()}).status_code == 200
    link_a = _link(outbox)
    _post(client, "/signup", {"email": "b@example.org", "code": code})
    link_b = _link(outbox)
    r, _ = _confirm(client, link_a)
    assert r.status_code == 303
    with platform_db() as db:
        c = db.scalar(select(SignupCode))
        fid = db.scalar(select(Membership.family_id).join(Parent, Parent.id == Membership.parent_id)
                        .where(Parent.username == "a"))
        assert c.used_at is not None and c.family_id == fid
    client.cookies.clear()
    assert "Einladungscode ist ungültig" in client.get(link_b).text           # Code schon eingelöst


def test_signup_link_is_signed_and_expires(client, monkeypatch, outbox):
    _mode(monkeypatch, "open")
    token = signup.make_token("x@example.org")
    assert signup.read_token(token) == ("x@example.org", "")
    payload, sig = token.split(".")
    assert signup.read_token(payload + "." + sig[:-2] + "AA") is None
    assert signup.read_token(signup.make_token("x@example.org", now=time.time() - 25 * 3600)) is None
    assert client.get("/signup/kaputt").status_code == 404


def test_signup_rate_limit_and_mail_required(client, monkeypatch):
    _mode(monkeypatch, "open")
    assert _post(client, "/signup", {"email": "a@example.org"}).status_code == 503   # ohne SMTP
    for _ in range(4):                                                        # 5 Anfragen je IP im Zeitfenster
        _post(client, "/signup", {"email": "a@example.org"})
    assert _post(client, "/signup", {"email": "a@example.org"}).status_code == 429
