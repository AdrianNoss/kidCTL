"""Newsletter: Opt-in je Familie (Einstellungen, Registrierung), Abmeldelink, Verwaltung mit Test und Versand."""

import re

import pyotp
import pytest
from sqlalchemy import select

from app import mail, newsletter, web
from app.config import Settings
from app.models import AdminLog, Family, Newsletter, Operator, Parent, utcnow
from app.tenant import family_session
from conftest import csrf_from, login, platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)
from test_signup import _link, _mode, _post


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, attachments=None, headers=None:
                        sent.append({"to": to, "subject": subject, "body": body, "headers": headers or {}}) or True)
    return sent


def _family1_mail(parent, email="mama@example.org"):
    with family_session(1) as db:
        p = db.get(Parent, parent.id)
        p.email, p.email_verified_at = email, utcnow()
        db.commit()


def _optin(family_id=1, on=True):
    with platform_db() as db:
        db.get(Family, family_id).newsletter_at = utcnow() if on else None
        db.commit()


def test_family_toggles_newsletter(client, parent, saas):
    login(client, parent)
    page = client.get("/settings").text
    assert 'id="newsletter"' in page and "Neuigkeiten erhalten" in page
    tok = csrf_from(page)
    r = client.post("/settings/newsletter", data={"csrf_token": tok, "subscribe": "1"}, follow_redirects=False)
    assert r.status_code == 303
    with platform_db() as db:
        assert db.get(Family, 1).newsletter_at is not None
    assert "Abonniert seit" in client.get("/settings").text
    client.post("/settings/newsletter", data={"csrf_token": tok})
    with platform_db() as db:
        assert db.get(Family, 1).newsletter_at is None
    assert client.post("/settings/newsletter", data={"subscribe": "1"}).status_code in (400, 403)   # CSRF


def test_signup_optin_default_off(client, monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, **kw: sent.append((to, subject, body)) or True)
    _mode(monkeypatch, "open")
    for email, checked in (("ohne@example.org", False), ("mit@example.org", True)):
        _post(client, "/signup", {"email": email})
        link = _link(sent)
        page = client.get(link)
        assert 'name="newsletter"' in page.text and 'name="newsletter" value="1" checked' not in page.text
        secret = re.search(r'name="secret" value="([A-Z2-7]+)"', page.text).group(1)
        data = {"csrf_token": csrf_from(page.text), "secret": secret, "password": "ein sicheres passwort",
                "repeat": "ein sicheres passwort", "totp": pyotp.TOTP(secret).now()}
        if checked:
            data["newsletter"] = "1"
        assert client.post(link, data=data, follow_redirects=False).status_code == 303
        client.cookies.clear()
    with platform_db() as db:
        rows = db.execute(select(Family.id, Family.newsletter_at).order_by(Family.id)).all()
    assert [bool(at) for _fid, at in rows[1:]] == [False, True]


def test_unsubscribe_link(client, parent, saas):
    _optin()
    url = newsletter.unsubscribe_url(1).replace("http://testserver", "")
    assert client.get(url.replace(url[-4:], "0000")).status_code == 404              # Signatur falsch
    page = client.get(url)
    assert page.status_code == 200 and "Abbestellen" in page.text
    with platform_db() as db:
        assert db.get(Family, 1).newsletter_at is not None                           # GET ändert nichts
    r = client.post(url, data={"List-Unsubscribe": "One-Click"})                       # RFC 8058, ohne Token
    assert r.status_code == 200 and "Abbestellt" in r.text
    with platform_db() as db:
        assert db.get(Family, 1).newsletter_at is None


def test_admin_newsletter_test_and_send(client, operator, parent, saas, outbox):  # noqa: F811
    _family1_mail(parent)
    _optin()
    admin_login(client, operator)
    tok = csrf_from(client.get("/admin/newsletter").text)
    r = client.post("/admin/newsletter", data={"csrf_token": tok}, follow_redirects=False)
    nid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/admin/newsletter/{nid}").text
    assert "An 1 Familie senden" in page
    data = {"csrf_token": tok, "subject_de": "Version 0.35", "body_de": "Neu: Newsletter.\nViel Spaß!",
            "subject_en": "Version 0.35", "body_en": "New: newsletter."}
    r = client.post(f"/admin/newsletter/{nid}", data={**data, "action": "test"})
    assert r.status_code == 400 and "E-Mail-Adresse hinterlegen" in r.text                # Betreiber ohne Adresse
    with platform_db() as db:
        db.get(Operator, operator.id).email = "op@example.org"
        db.commit()
    r = client.post(f"/admin/newsletter/{nid}", data={**data, "action": "test"}, follow_redirects=False)
    assert r.headers["location"].endswith("saved=test")
    assert [m["subject"] for m in outbox] == ["[Test] Version 0.35", "[Test] Version 0.35"]  # DE und EN
    assert all(m["to"] == "op@example.org" for m in outbox)
    outbox.clear()
    with platform_db() as db:                                                         # zweite Familie ohne Zustimmung
        other = Family()
        db.add(other)
        db.commit()
    r = client.post(f"/admin/newsletter/{nid}/send", data={"csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 303                                                       # Hintergrundaufgabe lief
    assert len(outbox) == 1 and outbox[0]["to"] == "mama@example.org"
    m = outbox[0]
    assert m["subject"] == "Version 0.35" and "Neu: Newsletter." in m["body"] and "/newsletter/unsubscribe/1/" in m["body"]
    assert m["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    with platform_db() as db:
        nl = db.get(Newsletter, nid)
        assert nl.status == "sent" and nl.families == 1 and nl.recipients == 1 and nl.sent_by == "betrieb"
        assert db.query(AdminLog).filter_by(object="newsletter").count() == 1
    assert client.post(f"/admin/newsletter/{nid}/send", data={"csrf_token": tok}).status_code == 409   # nur einmal
    assert client.post(f"/admin/newsletter/{nid}", data=data).status_code == 409                       # unveränderlich
    assert client.post(f"/admin/newsletter/{nid}/delete", data={"csrf_token": tok}).status_code == 409
    assert "Version 0.35" in client.get("/admin/newsletter").text


def test_english_parent_gets_english_or_fallback(client):
    nl = Newsletter(subject_de="Neu", body_de="Text", subject_en="New", body_en="Text EN")
    subject, body = newsletter.compose(nl, "en", "Mum", 1)
    assert subject == "New" and body.startswith("Hello Mum") and "Text EN" in body
    nl.subject_en = nl.body_en = ""
    subject, body = newsletter.compose(nl, "en", "Mum", 1)
    assert subject == "Neu" and body.startswith("Hallo Mum")


def test_send_requires_content_and_support_role(client, operator, saas):  # noqa: F811
    admin_login(client, operator)
    tok = csrf_from(client.get("/admin/newsletter").text)
    nid = int(client.post("/admin/newsletter", data={"csrf_token": tok}, follow_redirects=False)
              .headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/admin/newsletter/{nid}/send", data={"csrf_token": tok})
    assert r.status_code == 400 and "Betreff und Text" in r.text
    client.post(f"/admin/newsletter/{nid}", data={"csrf_token": tok, "subject_de": "A", "body_de": "B", "subject_en": "C"})
    assert client.post(f"/admin/newsletter/{nid}/send", data={"csrf_token": tok}).status_code == 400   # EN halb
    assert client.post(f"/admin/newsletter/{nid}/delete", data={"csrf_token": tok}, follow_redirects=False).status_code == 303
    with platform_db() as db:
        db.get(Operator, operator.id).role = "support"
        db.commit()
    assert client.get("/admin/newsletter", follow_redirects=False).status_code == 403
