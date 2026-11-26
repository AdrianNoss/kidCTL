"""„Feature vorschlagen“: nur mit FEATURE_REQUEST_EMAIL, Validierung, festes Mailformat v1, Rate-Limit, kein Inhalt in
der Datenbank."""

import re
from dataclasses import replace

import pytest
from sqlalchemy import func, select, text

from app import feature_requests as fr, mail
from app.config import Settings, settings
from app.db import engine
from app.models import LoginAttempt, Parent, utcnow
from conftest import SessionLocal, csrf_from, login

TO = "features@example.org"
V1 = re.compile(r"\AKIDCTL-FEATURE-REQUEST v1\nID: (?P<id>[0-9a-f-]{36})\nDatum: (?P<date>\S+)\nKategorie: (?P<cat>[^\n]+)\n"
                r"Wichtigkeit: (?P<imp>[^\n]+)\nKidCTL-Version: (?P<ver>[^\n]+)\nKontakt: (?P<contact>[^\n]+)\n---\n"
                r"Titel: (?P<title>[^\n]+)\n---\n(?P<body>.*)\Z", re.S)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(Settings, "feature_requests", property(lambda s: True))
    monkeypatch.setattr(fr, "settings", replace(settings, feature_request_email=TO))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, **kw: sent.append(
        dict(to=to, subject=subject, body=body, html=html, headers=kw.get("headers"))) or True)
    return sent


def _post(client, **data):
    tok = csrf_from(client.get("/feature-request").text)
    form = {"csrf_token": tok, "title": "Nintendo Switch", "category": "platform",
            "description": "Bitte die Switch anbinden."} | data
    return client.post("/feature-request", data=form, follow_redirects=False)


def _verified(parent, email="mama@example.org"):
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        p.email, p.email_verified_at = email, utcnow()
        db.commit()


def test_off_without_recipient(client, parent):
    login(client, parent)
    assert client.get("/feature-request").status_code == 404
    assert 'href="/feature-request"' not in client.get("/settings").text


def test_requires_login(client, outbox):
    assert client.get("/feature-request", follow_redirects=False).status_code in (303, 307, 401)


def test_mail_format_v1_without_contact(client, parent, outbox):
    _verified(parent)
    login(client, parent)
    assert 'href="/feature-request"' in client.get("/settings").text
    r = _post(client, title="  Switch\r\n<b>anbinden</b>  ", importance="high",
              description="Erste Zeile\r\n---\n<script>x</script>Zweite\x07 Zeile\n")
    assert r.status_code == 303 and r.headers["location"] == "/feature-request?sent=1"
    assert "Danke, dein Vorschlag ist angekommen." in client.get(r.headers["location"]).text
    [m] = outbox
    assert m["to"] == TO and m["subject"] == "[KidCTL Feature] Switch anbinden"
    v = V1.match(m["body"])
    assert v, m["body"]
    assert v["cat"] == "Neue Plattform/Gerät" and v["imp"] == "hoch" and v["contact"] == "-"
    assert v["title"] == "Switch anbinden" and v["body"] == "Erste Zeile\n---\nxZweite Zeile"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", v["date"]) and v["ver"]
    assert m["headers"] is None and "mama@example.org" not in m["body"] + m["html"]
    assert "Zweite Zeile" in m["html"] and "<script>" not in m["html"]
    with engine.connect() as c:                                              # kein Inhalt in der Datenbank
        assert c.execute(text("SELECT count(*) FROM login_attempt WHERE kind = 'feature'")).scalar() == 1


def test_contact_only_when_allowed(client, parent, outbox):
    _verified(parent)
    login(client, parent)
    _post(client, contact="1")
    v = V1.match(outbox[-1]["body"])
    assert v["contact"] == "mama@example.org" and v["imp"] == "-"
    assert outbox[-1]["headers"] == {"Reply-To": "mama@example.org"}


def test_validation(client, parent, outbox):
    login(client, parent)
    for data in ({"title": " <i></i> "}, {"description": ""}, {"category": "gibtsnicht"},
                 {"importance": "sehr"}, {"title": "x" * (fr.MAX_TITLE + 1)},
                 {"description": "x" * (fr.MAX_DESCRIPTION + 1)}):
        r = _post(client, **data)
        assert r.status_code == 400, data
        assert r.text.count('class="error"') == 1
    assert outbox == []
    assert "Nintendo Switch" in _post(client, category="").text                 # Eingaben bleiben erhalten


def test_csrf(client, parent, outbox):
    login(client, parent)
    r = client.post("/feature-request", data={"csrf_token": "falsch", "title": "a", "category": "other",
                                              "description": "b"}, follow_redirects=False)
    assert r.status_code == 403 and outbox == []


def test_rate_limit_per_parent(client, parent, outbox):
    login(client, parent)
    for _ in range(fr.DAILY_LIMIT):
        assert _post(client).status_code == 303
    r = _post(client)
    assert r.status_code == 400 and "morgen" in r.text
    assert len(outbox) == fr.DAILY_LIMIT
    with SessionLocal() as db:                                               # nach 24 Stunden wieder frei
        for a in db.scalars(select(LoginAttempt).where(LoginAttempt.kind == "feature")):
            a.at = a.at.replace(year=a.at.year - 1)
        db.commit()
    assert _post(client).status_code == 303


def test_failed_send_does_not_count(client, parent, outbox, monkeypatch):
    login(client, parent)
    monkeypatch.setattr(mail, "send", lambda *a, **kw: False)
    r = _post(client)
    assert r.status_code == 400 and "später" in r.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.kind == "feature")) == 0


def test_english_ui(client, parent, outbox):
    with SessionLocal() as db:
        db.get(Parent, parent.id).language = "en"
        db.commit()
    login(client, parent)
    page = client.get("/feature-request").text
    assert "Suggest a feature" in page and "New platform/device" in page
    _post(client, importance="low")
    v = V1.match(outbox[-1]["body"])
    assert v["cat"] == "Neue Plattform/Gerät" and v["imp"] == "niedrig"      # Mailformat bleibt deutsch
