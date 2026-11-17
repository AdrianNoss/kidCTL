"""Etappe 1.4: Anmeldung per E-Mail (systemweit eindeutig, mit Bestätigung) und Betriebsart KIDCTL_MODE."""

import re

import pyotp
import pytest

from app import config, mail, web
from app.config import Settings
from app.models import ROLE_OWNER, Membership, Parent, utcnow
from app.security import hash_password, new_totp_secret
from app.tenant import family_session
from conftest import PASSWORD, SessionLocal, add_family, add_parent, csrf_from, login


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None: sent.append((to, subject, body)) or True)
    return sent


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _login(client, ident, secret, password=PASSWORD):
    page = client.get("/login")
    return client.post("/login", data={"csrf_token": csrf_from(page.text), "username": ident, "password": password,
                                       "totp": pyotp.TOTP(secret).now()}, follow_redirects=False)


def _profile(client, **data):
    tok = csrf_from(client.get("/profile").text)
    form = {"csrf_token": tok, "username": "mama", "display_name": "", "email": "", "language": "",
            "current_password": PASSWORD, **data}
    return client.post("/profile", data=form, follow_redirects=False)


def test_new_email_needs_confirmation_before_login(client, parent, outbox):
    login(client, parent)
    assert _profile(client, email="Mama@Example.org").status_code == 303
    to, subject, body = outbox[-1]
    assert to == "Mama@Example.org" and "bestätigen" in subject
    page = client.get("/profile").text
    assert "nicht bestätigt" in page and "Bestätigungslink erneut senden" in page
    client.cookies.clear()
    assert _login(client, "mama@example.org", parent.totp_secret).status_code == 401   # noch nicht bestätigt
    link = re.search(r"http://testserver(/email/verify/\S+)", body).group(1)
    r = client.get(link, follow_redirects=False)
    assert r.headers["location"] == "/login?msg=email"
    assert client.get(link, follow_redirects=False).headers["location"] == "/login?msg=email_invalid"  # einmalig
    with SessionLocal() as db:
        db.get(Parent, parent.id).totp_last_step = 0
        db.commit()
    assert _login(client, "MAMA@example.org", parent.totp_secret).status_code == 303   # ohne Groß/klein
    client.cookies.clear()
    assert login(client, parent).status_code == 303                                      # Benutzername geht weiter


def test_email_is_unique_across_families(client, parent):
    fid = add_family("Andere")
    with family_session(fid) as db:
        p = Parent(username="fremd", email="eltern@example.org", email_verified_at=utcnow(),
                   password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(p)
        db.flush()
        db.add(Membership(parent_id=p.id, role=ROLE_OWNER))
        db.commit()
    login(client, parent)
    r = _profile(client, email="Eltern@Example.org")
    assert r.status_code == 400 and "wird schon verwendet" in r.text
    tok = csrf_from(client.get("/parents").text)
    r = client.post("/parents/invite", data={"csrf_token": tok, "email": "eltern@example.org"})
    assert r.status_code == 400 and "wird schon verwendet" in r.text


def test_without_smtp_email_counts_as_confirmed(client, parent):
    login(client, parent)
    assert _profile(client, email="mama@example.org").status_code == 303
    with SessionLocal() as db:
        me = db.get(Parent, parent.id)
        assert me.email_verified_at is not None
        me.totp_last_step = 0   # Replay-Schutz: derselbe Code wie beim ersten Login
        db.commit()
    client.cookies.clear()
    assert _login(client, "mama@example.org", parent.totp_secret).status_code == 303


def test_saas_requires_email_and_derives_username(client, parent, saas):
    login(client, parent)
    page = client.get("/parents").text
    tok = csrf_from(page)
    r = client.post("/parents/invite", data={"csrf_token": tok, "email": ""})
    assert r.status_code == 400 and "Bitte eine E-Mail-Adresse angeben." in r.text
    add_parent(username="papa")
    r = client.post("/parents/invite", data={"csrf_token": tok, "email": "papa@example.org"})
    link = re.search(r"http://testserver(/invite/[^<\s]+)", r.text).group(1)
    client.cookies.clear()
    form = client.get(link).text
    assert 'name="username"' not in form                                        # kein Benutzername im SaaS
    r = client.post(link, data={"csrf_token": csrf_from(form), "password": "x" * 12, "repeat": "x" * 12},
                    follow_redirects=False)
    assert r.status_code == 303
    login(client, parent)
    with SessionLocal() as db:
        names = {p.email: p.username for p in db.query(Parent).all()}
    assert names["papa@example.org"] == "papa2"                                    # abgeleitet, eindeutig
    assert _profile(client, email="").status_code == 400                            # Pflicht im SaaS


def test_mode_setting(monkeypatch):
    monkeypatch.setenv("KIDCTL_MODE", "saas")
    assert config._mode() == "saas"
    monkeypatch.setenv("KIDCTL_MODE", "")
    assert config._mode() == "single"
    monkeypatch.setenv("KIDCTL_MODE", "cloud")
    with pytest.raises(RuntimeError, match="KIDCTL_MODE"):
        config._mode()
