"""Etappe 5: Abo-Vorbereitung – Testphase, Schonfrist, Ruhemodus, Löschung, Verwaltungsbereich, Webhook."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import billing, mail, policy, web
from app.config import Settings
from app.models import Child, Device, Family, utcnow
from conftest import PASSWORD, SessionLocal, csrf_from, login, platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, **kw: sent.append((to, subject, body)) or True)
    return sent


@pytest.fixture
def pushed(monkeypatch):
    calls = []

    async def fake(db, ids):
        calls.append(set(ids))
    monkeypatch.setattr(policy, "push_policies", fake)
    return calls


def _family(**kw):
    with platform_db() as db:
        f = db.get(Family, 1)
        for k, v in kw.items():
            setattr(f, k, v)
        db.commit()


def _get():
    with platform_db() as db:
        return db.get(Family, 1)


def _mail(parent):
    from app.models import Parent
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        p.email, p.email_verified_at = "mama@example.org", utcnow()
        db.commit()


def _check(now=None):
    with platform_db() as db:
        return billing.check_family(db, 1, now)


def test_existing_families_are_free_and_single_mode_never_expires(client, parent):
    f = _get()
    assert (f.plan, f.billing_status) == ("free", "active")
    _family(plan="standard", billing_status="trial", trial_ends_at=utcnow() - timedelta(days=200))
    assert billing.state(_get()).phase == "ok"                                  # Einzelbetrieb: keine Fristen
    assert _check() == ""


def test_trial_grace_pause_resume(client, parent, saas, outbox, pushed):
    _mail(parent)
    with SessionLocal() as db:
        db.add(Device(name="PC", token_hash="d" * 64))
        db.commit()
    now = utcnow()
    _family(plan="standard", billing_status="trial", trial_ends_at=now - timedelta(days=1))
    assert billing.state(_get()).phase == "grace"
    assert _check() == "" and len(outbox) == 1 and "pausiert" in outbox[0][2]   # erster Hinweis (14 Tage)
    assert _check() == "" and len(outbox) == 1                                  # nur einmal
    assert _check(now + timedelta(days=7)) == "" and len(outbox) == 2           # 7 Tage vorher
    login(client, parent)
    assert "Euer Abo ist abgelaufen" in client.get("/").text                    # Banner

    assert _check(now + timedelta(days=14)) == "paused" and _get().paused_at is not None
    assert "gelöscht" in outbox[-1][2]
    r = client.get("/children", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#abo"    # Ruhemodus: nur Einstellungen
    page = client.get("/settings").text
    assert "KidCTL ist pausiert" in page and 'action="/settings/export"' in page
    tok = csrf_from(page)
    assert client.post("/children", data={"csrf_token": tok, "name": "X"}, follow_redirects=False).headers["location"] == "/settings#abo"
    import asyncio
    asyncio.run(billing.push_changed({1: "paused"}))
    assert pushed and len(pushed[-1]) == 1                                         # Pause an die Agenten


def test_operator_extends_and_resumes(client, parent, operator, saas, outbox, pushed):  # noqa: F811
    with SessionLocal() as db:
        db.add(Device(name="PC", token_hash="d" * 64))
        db.commit()
    now = utcnow()
    _family(plan="standard", billing_status="trial", trial_ends_at=now - timedelta(days=20), paused_at=now)
    admin_login(client, operator)
    page = client.get("/admin").text
    assert "/admin/families/1" in page
    tok = csrf_from(client.get("/admin/families/1").text)
    r = client.post("/admin/families/1", data={"csrf_token": tok, "plan": "standard", "status": "active",
                                               "paid_until": (now + timedelta(days=365)).date().isoformat()},
                    follow_redirects=False)
    assert r.status_code == 303
    f = _get()
    assert f.paused_at is None and f.billing_status == "active" and pushed and len(pushed[-1]) == 1
    assert billing.state(f).phase == "ok"
    r = client.post("/admin/families/1", data={"csrf_token": tok, "plan": "gold", "status": "active"})
    assert r.status_code == 400


def test_automatic_deletion_with_warnings(client, parent, saas, outbox, pushed):
    _mail(parent)
    now = utcnow()
    paused = now - timedelta(days=61)
    _family(plan="standard", billing_status="canceled", paid_until=now - timedelta(days=80), paused_at=paused)
    assert _check() == "" and "endgültig gelöscht" in outbox[-1][2]               # 30 Tage vorher
    n = len(outbox)
    assert _check(paused + timedelta(days=84)) == "" and len(outbox) == n + 1      # 7 Tage vorher
    assert _check(paused + timedelta(days=89, hours=12)) == "" and len(outbox) == n + 2   # 1 Tag vorher
    assert _check(paused + timedelta(days=90, minutes=1)) == "deleted"
    with platform_db() as db:
        assert db.get(Family, 1) is None


def test_signup_starts_trial_or_free_code(client, monkeypatch, saas, outbox):
    from app import operators
    from test_signup import _confirm, _link, _mode, _post
    _mode(monkeypatch, "invite")
    with platform_db() as db:
        free = operators.create_code(db, "Oma", None, "free")
        trial = operators.create_code(db, "Test", None)
    for email, code in (("frei@example.org", free), ("test@example.org", trial)):
        _post(client, "/signup", {"email": email, "code": code})
        r, _ = _confirm(client, _link(outbox))
        assert r.status_code == 303
        client.cookies.clear()
    with platform_db() as db:
        fams = db.scalars(select(Family).order_by(Family.id.desc()).limit(2)).all()
        trial_f, free_f = fams
        assert (free_f.plan, free_f.billing_status) == ("free", "active")
        assert (trial_f.plan, trial_f.billing_status) == ("standard", "trial")
        assert timedelta(days=29) < trial_f.trial_ends_at - utcnow() <= timedelta(days=30)


def test_webhook(client, saas, monkeypatch, pushed):
    url = "/api/billing/webhook"
    assert client.post(url, json={"status": "active", "family_id": 1}).status_code == 404   # ohne Geheimnis aus
    monkeypatch.setattr(Settings, "billing_webhook_secret", property(lambda s: "geheim"))
    assert client.post(url, json={"status": "active", "family_id": 1}).status_code == 401
    auth = {"Authorization": "Bearer geheim"}
    _family(plan="standard", billing_status="trial", trial_ends_at=utcnow() - timedelta(days=30), paused_at=utcnow())
    r = client.post(url, headers=auth, json={"status": "active", "family_id": 1, "provider": "pay",
                                             "customer": "C-1", "paid_until": "2099-01-01"})
    assert r.status_code == 200 and r.json()["result"] == "resumed" and _get().paused_at is None
    r = client.post(url, headers=auth, json={"status": "past_due", "customer": "C-1", "provider": "pay"})
    assert r.status_code == 200 and _get().billing_status == "past_due"
    assert billing.state(_get()).phase == "ok"                                    # bezahlt bis 2099 bleibt
    assert client.post(url, headers=auth, json={"status": "kaputt", "family_id": 1}).status_code == 422
    assert client.post(url, headers=auth, json={"status": "active", "customer": "unbekannt"}).status_code == 404


def test_child_limit(client, parent, saas, monkeypatch):
    monkeypatch.setitem(billing.MAX_CHILDREN, "free", 1)
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    assert client.post("/children", data={"csrf_token": tok, "name": "A"}, follow_redirects=False).status_code == 303
    r = client.post("/children", data={"csrf_token": tok, "name": "B"})
    assert r.status_code == 400 and "höchstens 1 Kinder" in r.text
    with SessionLocal() as db:
        assert db.scalar(select(Child.name).where(Child.name == "B")) is None


def test_settings_shows_plan(client, parent, saas):
    login(client, parent)
    page = client.get("/settings").text
    assert 'id="abo"' in page and "Tarif: Kostenlos" in page
    assert re.search(r"Online-Bezahlung", page) is None                           # kostenlos: kein Hinweis
    assert PASSWORD
