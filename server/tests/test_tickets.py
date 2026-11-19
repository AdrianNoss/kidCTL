"""Verwaltungsbereich 2, Etappe 4: Support-Tickets."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app import mail, tickets, web
from app.config import Settings
from app.db import engine
from app.models import AdminLog, Family, Operator, Parent, Ticket, TicketMessage, utcnow
from app.security import hash_password, new_totp_secret
from app.tenant import family_session
from conftest import PASSWORD, SessionLocal, add_family, csrf_from, login, platform_db
from test_admin_roles import _login, _operator

SECRET = "Mein Passwort ist geheim123"


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


def _new_ticket(client, subject="Agent startet nicht", body=SECRET, category="tech"):
    tok = csrf_from(client.get("/support").text)
    r = client.post("/support", data={"csrf_token": tok, "subject": subject, "category": category, "body": body},
                    follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def _status(tid):
    with platform_db() as db:
        return db.get(Ticket, tid).status


def _set(tid, **kw):
    with platform_db() as db:
        t = db.get(Ticket, tid)
        for k, v in kw.items():
            setattr(t, k, v)
        db.commit()


def test_menu_only_in_saas(client, parent):
    login(client, parent)
    assert 'href="/support"' not in client.get("/").text
    assert client.get("/support").status_code == 404


def test_lifecycle_and_notifications(client, parent, saas, outbox):
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        p.email, p.email_verified_at = "mama@example.org", utcnow()
        db.commit()
    with platform_db() as db:
        db.add(Operator(username="mit-mails", password_hash="x", totp_secret="x", email="op@example.org", ticket_mails=True))
        db.add(Operator(username="ohne", password_hash="x", totp_secret="x", email="nein@example.org"))
        db.commit()
    login(client, parent)
    assert 'href="/support"' in client.get("/").text
    tid = _new_ticket(client)
    assert _status(tid) == "new"
    assert [m[0] for m in outbox] == ["op@example.org"]                       # nur Betreiber mit Ticket-Mails
    assert SECRET not in outbox[-1][2] and "Agent startet" not in outbox[-1][2] and f"/admin/tickets/{tid}" in outbox[-1][2]
    with engine.connect() as c:
        raw = c.execute(text("SELECT subject FROM ticket")).scalar() + c.execute(text("SELECT body FROM ticket_message")).scalar()
    assert "Agent" not in raw and "geheim" not in raw                          # Plattform-Schlüssel

    client.cookies.clear()
    _login(client, _operator("support"))                                       # Support darf Tickets bearbeiten
    page = client.get(f"/admin/tickets/{tid}").text
    assert SECRET in page and "Technik" in page and "Agent startet nicht" in page
    tok = csrf_from(page)
    outbox.clear()
    client.post(f"/admin/tickets/{tid}", data={"csrf_token": tok, "body": "Interner Hinweis XYZ", "internal": "1"})
    assert _status(tid) == "new" and outbox == []                              # interne Notiz: nichts
    client.post(f"/admin/tickets/{tid}", data={"csrf_token": tok, "body": "Bitte neu starten", "status": "waiting"})
    assert _status(tid) == "waiting"
    assert [m[0] for m in outbox] == ["mama@example.org"] and "Bitte neu starten" not in outbox[-1][2]
    client.post(f"/admin/tickets/{tid}", data={"csrf_token": tok, "body": "Noch was", "status": "in_progress"})
    assert _status(tid) == "in_progress"

    client.cookies.clear()
    login(client, parent)
    page = client.get(f"/support/{tid}").text
    assert "Bitte neu starten" in page and "Interner Hinweis XYZ" not in page  # interne Notiz unsichtbar
    tok = csrf_from(page)
    outbox.clear()
    client.post(f"/support/{tid}", data={"csrf_token": tok, "body": "Hat geklappt"})
    assert _status(tid) == "in_progress" and [m[0] for m in outbox] == ["op@example.org"]
    client.post(f"/support/{tid}/close", data={"csrf_token": tok})
    assert _status(tid) == "closed"
    client.post(f"/support/{tid}", data={"csrf_token": tok, "body": "Doch nicht"})   # innerhalb 14 Tage: wieder offen
    assert _status(tid) == "in_progress"
    _set(tid, status="closed", closed_at=utcnow() - timedelta(days=15))
    r = client.post(f"/support/{tid}", data={"csrf_token": tok, "body": "Spät"})
    assert r.status_code == 400 and "neues Ticket" in r.text and _status(tid) == "closed"
    with platform_db() as db:
        changes = [(e.actor, e.new) for e in db.scalars(select(AdminLog).where(AdminLog.object == "ticket",
                                                                               AdminLog.field == "status"))]
    assert ("op-support", "waiting") in changes and ("Familie", "in_progress") in changes and ("Familie", "closed") in changes


def test_auto_close_after_14_days(client, parent, saas, outbox):
    login(client, parent)
    tid = _new_ticket(client)
    _set(tid, status="waiting", updated_at=utcnow() - timedelta(days=15))
    with platform_db() as db:
        assert tickets.auto_close(db) == [(1, tid)]
    assert _status(tid) == "closed"
    with platform_db() as db:
        entries = db.scalars(select(AdminLog).where(AdminLog.object == "ticket", AdminLog.field == "status")).all()
        assert [(e.actor, e.new) for e in entries] == [("System", "closed")]   # Werte verschlüsselt: in Python prüfen
    _set(tid, status="waiting", updated_at=utcnow() - timedelta(days=3), closed_at=None)
    with platform_db() as db:
        assert tickets.auto_close(db) == []


def test_isolation_between_families(client, parent, saas):
    login(client, parent)
    tid = _new_ticket(client)
    other = add_family("Andere")
    with family_session(other) as db:
        p = Parent(username="fremd", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret())
        db.add(p)
        db.flush()
        from app.models import ROLE_OWNER, Membership
        db.add(Membership(parent_id=p.id, role=ROLE_OWNER))
        db.commit()
        import pyotp
        secret = p.totp_secret
    client.cookies.clear()
    page = client.get("/login").text
    client.post("/login", data={"csrf_token": csrf_from(page), "username": "fremd", "password": PASSWORD,
                                "totp": pyotp.TOTP(secret).now()})
    assert client.get(f"/support/{tid}").status_code == 404
    assert f"/support/{tid}" not in client.get("/support").text
    tok = csrf_from(client.get("/support").text)
    assert client.post(f"/support/{tid}", data={"csrf_token": tok, "body": "x"}).status_code == 404
    assert client.post(f"/support/{tid}/close", data={"csrf_token": tok}).status_code == 404


def test_paused_family_reaches_support(client, parent, saas):
    with platform_db() as db:
        f = db.get(Family, 1)
        f.plan, f.billing_status, f.trial_ends_at, f.paused_at = "standard", "trial", utcnow() - timedelta(days=30), utcnow()
        db.commit()
    login(client, parent)
    assert client.get("/children", follow_redirects=False).status_code == 303
    assert client.get("/support").status_code == 200 and _new_ticket(client)


def test_admin_list_filters_and_tile(client, parent, saas):
    login(client, parent)
    a = _new_ticket(client, subject="Rechnung falsch", category="billing")
    b = _new_ticket(client, subject="Agent hängt")
    _set(b, status="closed", closed_at=utcnow())
    client.cookies.clear()
    _login(client, _operator("admin"))
    ids = lambda html: [int(x) for x in re.findall(r'href="/admin/tickets/(\d+)"><strong>', html)]  # noqa: E731
    assert ids(client.get("/admin/tickets").text) == [a]                         # Standard: offen
    assert ids(client.get("/admin/tickets?status=all").text) == [a, b]           # älteste zuerst
    assert ids(client.get("/admin/tickets?status=all&sort=-created").text) == [b, a]
    assert ids(client.get("/admin/tickets?status=all&category=tech").text) == [b]
    assert ids(client.get("/admin/tickets?status=all&q=RECHNUNG").text) == [a]
    assert ids(client.get("/admin/tickets?status=all&family=%2399").text) == []
    tiles = dict(re.findall(r'tile-label">([^<]+)</div><div class="tile-value">(\d+)', client.get("/admin").text))
    assert tiles["offene Tickets"] == "1"
    assert f"/admin/tickets/{a}" in client.get("/admin/families/1").text


def test_deleted_with_family_and_retention(client, parent, saas):
    from app import retention
    login(client, parent)
    tid = _new_ticket(client)
    old = _new_ticket(client)
    _set(old, status="closed", closed_at=utcnow() - timedelta(days=400))
    with platform_db() as db:
        retention.prune_all(db)
        assert db.get(Ticket, old) is None and db.get(Ticket, tid) is not None
        from app.tenant import delete_family
        delete_family(db, 1)
        assert db.scalar(select(Ticket)) is None and db.scalar(select(TicketMessage)) is None
