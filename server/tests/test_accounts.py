"""Profil, Passwort vergessen (E-Mail-Link), Authenticator wechseln/zurücksetzen."""

import re

import pyotp
import pytest

from app import mail
from app.config import Settings
from app.db import SessionLocal
from app.models import Parent, ParentSession
from app.security import hash_password, new_totp_secret
from conftest import PASSWORD, csrf_from, login

NEW_PW = "ganz neues passwort 42"


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _set(parent, **kw):
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        for k, v in kw.items():
            setattr(p, k, v)
        db.commit()


def _get(parent_id) -> Parent:
    with SessionLocal() as db:
        return db.get(Parent, parent_id)


def _anon_post(client, url, data):
    page = client.get(url)
    return client.post(url, data={"csrf_token": csrf_from(page.text), **data}, follow_redirects=False)


def _login_as(client, p: Parent, password=PASSWORD, secret=None):
    return login(client, type("P", (), {"id": p.id, "username": p.username, "totp_secret": secret or p.totp_secret}),
                 password=password)


def test_password_reset_by_mail(client, parent, outbox):
    _set(parent, email="Mama@Example.org")
    r = _anon_post(client, "/reset", {"ident": "unbekannt"})
    assert r.status_code == 200 and "Falls ein Account" in r.text and outbox == []
    r = _anon_post(client, "/reset", {"ident": "mama@example.org"})       # E-Mail, ohne Groß/klein
    assert "Falls ein Account" in r.text and len(outbox) == 1
    to, subject, body = outbox[0]
    link = re.search(r"http://testserver(/reset/\S+)", body).group(1)
    assert to == "Mama@Example.org" and "Passwort" in subject

    assert _anon_post(client, link, {"password": NEW_PW, "repeat": "anders"}).status_code == 400
    assert _anon_post(client, link, {"password": "kurz", "repeat": "kurz"}).status_code == 400
    r = _anon_post(client, link, {"password": NEW_PW, "repeat": NEW_PW})
    assert r.status_code == 303 and r.headers["location"] == "/login?msg=reset"
    assert "Passwort geändert" in client.get("/login?msg=reset").text
    assert client.get(link).status_code == 404                              # nur einmal verwendbar
    assert login(client, parent).status_code == 401                         # altes Passwort
    assert login(client, parent, password=NEW_PW).status_code == 303        # neues + Authenticator
    assert any("Passwort geändert" in s for _, s, _b in outbox)             # Hinweis-Mail


def test_password_reset_needs_mail_and_is_rate_limited(client, parent, outbox, monkeypatch):
    for _ in range(5):
        _anon_post(client, "/reset", {"ident": "mama"})
    assert outbox == []                                                     # keine E-Mail hinterlegt
    assert _anon_post(client, "/reset", {"ident": "mama"}).status_code == 429
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: False))
    page = client.get("/reset").text
    assert "kein E-Mail-Versand" in page and 'name="ident"' not in page
    assert client.get("/reset/ungueltig").status_code == 404


def test_profile_names_and_email(client, parent, outbox):
    assert login(client, parent).status_code == 303
    page = client.get("/profile").text
    tok = csrf_from(page)
    # Anzeigename ohne Passwort
    r = client.post("/profile", data={"csrf_token": tok, "username": "mama", "display_name": "Mama",
                                      "email": ""}, follow_redirects=False)
    assert r.status_code == 303 and _get(parent.id).display_name == "Mama"
    # Benutzername/E-Mail nur mit aktuellem Passwort, gültig und eindeutig
    data = {"csrf_token": tok, "username": "mutter", "display_name": "Mama", "email": "m@example.org"}
    assert client.post("/profile", data={**data, "current_password": "falsch"}).status_code == 400
    assert client.post("/profile", data={**data, "email": "keine-mail", "current_password": PASSWORD}).status_code == 400
    assert client.post("/profile", data={**data, "username": "a b", "current_password": PASSWORD}).status_code == 400
    with SessionLocal() as db:
        db.add(Parent(username="papa", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret()))
        db.commit()
    assert client.post("/profile", data={**data, "username": "Papa", "current_password": PASSWORD}).status_code == 400
    r = client.post("/profile", data={**data, "current_password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    p = _get(parent.id)
    assert (p.username, p.email) == ("mutter", "m@example.org")
    assert "Mama" in client.get("/").text                                   # Name oben rechts


def test_profile_password_change_ends_other_sessions(client, parent, outbox):
    assert login(client, parent).status_code == 303
    with SessionLocal() as db:   # zweites Gerät
        db.add(ParentSession(parent_id=parent.id, token_hash="x" * 64, csrf_token="y",
                             expires_at=__import__("datetime").datetime(2099, 1, 1)))
        db.commit()
    tok = csrf_from(client.get("/profile").text)
    assert client.post("/profile/password", data={"csrf_token": tok, "current_password": "falsch",
                                                  "password": NEW_PW, "repeat": NEW_PW}).status_code == 400
    r = client.post("/profile/password", data={"csrf_token": tok, "current_password": PASSWORD,
                                               "password": NEW_PW, "repeat": NEW_PW}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.query(ParentSession).filter_by(parent_id=parent.id).count() == 1   # nur diese Sitzung
    assert client.get("/profile").status_code == 200                                # bleibt angemeldet


def test_profile_totp_change(client, parent, outbox):
    assert login(client, parent).status_code == 303
    tok = csrf_from(client.get("/profile").text)
    page = client.post("/profile/totp", data={"csrf_token": tok}).text
    secret = re.search(r"Schlüssel <code>([A-Z2-7]+)</code>", page).group(1)
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    assert _get(parent.id).totp_secret == parent.totp_secret               # alter gilt bis zur Bestätigung
    bad = client.post("/profile/totp/confirm", data={"csrf_token": tok, "token": token,
                                                     "current_password": PASSWORD, "totp": "000000"})
    assert bad.status_code == 400 and "passt nicht" in bad.text
    ok = client.post("/profile/totp/confirm", data={"csrf_token": tok, "token": token, "current_password": PASSWORD,
                                                    "totp": pyotp.TOTP(secret).now()}, follow_redirects=False)
    assert ok.status_code == 303 and _get(parent.id).totp_secret == secret


def test_other_parent_resets_lost_authenticator(client, parent, outbox):
    with SessionLocal() as db:
        papa = Parent(username="papa", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret(),
                      email="papa@example.org")
        db.add(papa)
        db.commit()
        papa_id, old_secret = papa.id, papa.totp_secret
    assert login(client, parent).status_code == 303
    tok = csrf_from(client.get("/parents").text)
    assert client.post(f"/parents/{parent.id}/totp-reset", data={"csrf_token": tok}).status_code == 400  # eigener
    page = client.post(f"/parents/{papa_id}/totp-reset", data={"csrf_token": tok}).text
    link = re.search(r"http://testserver(/totp-setup/[^<\s]+)", page).group(1)
    assert _get(papa_id).totp_secret != old_secret                         # alter sofort ungültig
    assert outbox and outbox[-1][0] == "papa@example.org" and "/totp-setup/" not in outbox[-1][2]  # Link nicht per Mail
    client.cookies.clear()

    setup = client.get(link).text
    secret = re.search(r"Schlüssel <code>([A-Z2-7]+)</code>", setup).group(1)
    assert _anon_post(client, link, {"password": "falsch", "totp": pyotp.TOTP(secret).now()}).status_code == 400
    r = _anon_post(client, link, {"password": PASSWORD, "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 303 and r.headers["location"] == "/login?msg=totp"
    assert client.get(link).status_code == 404
    papa = _get(papa_id)
    papa.totp_last_step = 0
    _set(papa, totp_last_step=0)
    assert _login_as(client, papa, secret=old_secret).status_code == 401
    assert _login_as(client, papa, secret=secret).status_code == 303
