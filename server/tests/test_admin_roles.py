"""Verwaltungsbereich 2, Etappe 1: Rollen Admin/Support (jede Route mit beiden Rollen) und Verwaltungsprotokoll."""

import re
from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import select

from app import web
from app.config import Settings
from app.models import AdminLog, Family, Operator, SignupCode, utcnow
from app.security import hash_password, new_totp_secret
from conftest import PASSWORD, csrf_from, platform_db


def _operator(role):
    with platform_db() as db:
        op = Operator(username=f"op-{role}", password_hash=hash_password(PASSWORD), totp_secret=new_totp_secret(),
                      role=role)
        db.add(op)
        db.commit()
        return op


def _login(client, op):
    page = client.get("/admin/login")
    r = client.post("/admin/login", data={"csrf_token": csrf_from(page.text), "username": op.username,
                                          "password": PASSWORD, "totp": pyotp.TOTP(op.totp_secret).now()},
                    follow_redirects=False)
    assert r.status_code == 303


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _code_id():
    with platform_db() as db:
        from app import operators
        operators.create_code(db, "x", None)
        return db.scalar(select(SignupCode.id))


# (Methode, Pfad, Formular, erlaubt für Support?)
ROUTES = [
    ("GET", "/admin", None, True),
    ("GET", "/admin/families/1", None, True),
    ("GET", "/admin/log", None, True),
    ("GET", "/admin/profile", None, True),
    ("POST", "/admin/profile", {"email": "op@example.org"}, True),
    ("GET", "/admin/codes", None, False),
    ("POST", "/admin/codes", {"note": "n"}, False),
    ("POST", "/admin/codes/{code}/delete", {}, False),
    ("POST", "/admin/families/1/name", {"name": "N"}, False),
    ("POST", "/admin/families/1", {"plan": "standard", "status": "active"}, False),
]


@pytest.mark.parametrize("role", ["admin", "support"])
def test_route_permissions(client, role):
    op = _operator(role)
    _login(client, op)
    tok = csrf_from(client.get("/admin/profile").text)
    for method, path, form, support_ok in ROUTES:
        path = path.replace("{code}", str(_code_id())) if "{code}" in path else path
        if method == "GET":
            r = client.get(path, follow_redirects=False)
        else:
            r = client.post(path, data={"csrf_token": tok, **form}, follow_redirects=False)
        allowed = role == "admin" or support_ok
        assert (r.status_code < 400) == allowed, (role, method, path, r.status_code)
    page = client.get("/admin").text
    assert ('href="/admin/codes"' in page) == (role == "admin")
    detail = client.get("/admin/families/1").text
    assert ('href="?edit=abo#abo"' in detail) == (role == "admin")
    assert ('action="/admin/families/1"' in client.get("/admin/families/1?edit=abo").text) == (role == "admin")


def test_changes_are_logged(client, monkeypatch, saas):
    op = _operator("admin")
    _login(client, op)
    tok = csrf_from(client.get("/admin/families/1").text)
    client.post("/admin/families/1/name", data={"csrf_token": tok, "name": "Familie Neu"})
    client.post("/admin/families/1", data={"csrf_token": tok, "plan": "standard", "status": "trial",
                                           "trial_ends": (utcnow() + timedelta(days=10)).date().isoformat()})
    client.post("/admin/codes", data={"csrf_token": tok, "note": "Oma", "free": "1"})
    with platform_db() as db:
        rows = {(e.actor, e.object, e.field): (e.old, e.new) for e in db.scalars(select(AdminLog))}
    assert rows[("op-admin", "family", "name")] == ("Testfamilie", "Familie Neu")
    assert rows[("op-admin", "subscription", "plan")] == ("free", "standard")
    assert rows[("op-admin", "subscription", "billing_status")] == ("active", "trial")
    assert ("op-admin", "code", "angelegt") in rows
    page = client.get("/admin/log?family=1").text
    assert "Familie Neu" in page and "op-admin" in page
    assert "Oma" not in client.get("/admin/log?family=1").text            # Code gehört zu keiner Familie
    assert "Oma" in client.get("/admin/log?obj=code").text

    # Webhook und System
    monkeypatch.setattr(Settings, "billing_webhook_secret", property(lambda s: "geheim"))
    client.post("/api/billing/webhook", headers={"Authorization": "Bearer geheim"},
                json={"status": "active", "family_id": 1, "paid_until": "2099-01-01"})
    with platform_db() as db:
        f = db.get(Family, 1)
        f.billing_status, f.paid_until = "canceled", utcnow() - timedelta(days=20)
        db.commit()
        from app import billing
        billing.check_family(db, 1)
        actors = {e.actor for e in db.scalars(select(AdminLog))}
        assert {"Webhook", "System"} <= actors
    assert "Webhook" in client.get("/admin/log?actor=Webhook").text


def test_log_is_invisible_to_families(client, parent):
    from conftest import login
    login(client, parent)
    assert client.get("/admin/log", follow_redirects=False).headers["location"] == "/admin/login"


def test_log_retention_keeps_invoice_entries(client):
    from app import retention
    old = utcnow() - timedelta(days=800)
    with platform_db() as db:
        db.add_all([AdminLog(at=old, actor="System", object="family", field="name"),
                    AdminLog(at=old, actor="System", object="invoice", field="ausgestellt")])
        db.commit()
        retention.prune_all(db)
        assert [e.object for e in db.scalars(select(AdminLog))] == ["invoice"]


def test_cli_roles(client, monkeypatch):
    from argparse import Namespace
    from app import cli
    monkeypatch.setenv("KIDCTL_OPERATOR_USER", "hilfe")
    monkeypatch.setenv("KIDCTL_OPERATOR_PASSWORD", "x" * 12)
    assert cli.create_operator(Namespace(reset=False, role="support", email="h@example.org")) == 0
    with platform_db() as db:
        op = db.scalar(select(Operator).where(Operator.username == "hilfe"))
        assert (op.role, op.email) == ("support", "h@example.org")
    assert cli.set_operator_role(Namespace(username="hilfe", role="admin")) == 0
    assert cli.set_operator_role(Namespace(username="niemand", role="admin")) == 1
    with platform_db() as db:
        assert db.scalar(select(Operator.role).where(Operator.username == "hilfe")) == "admin"
        assert db.scalar(select(AdminLog).where(AdminLog.field == "role")) is not None


def test_operator_profile(client):
    op = _operator("support")
    _login(client, op)
    tok = csrf_from(client.get("/admin/profile").text)
    assert client.post("/admin/profile", data={"csrf_token": tok, "ticket_mails": "1"}).status_code == 400
    client.post("/admin/profile", data={"csrf_token": tok, "email": "s@example.org", "ticket_mails": "1"})
    with platform_db() as db:
        o = db.get(Operator, op.id)
        assert (o.email, o.ticket_mails) == ("s@example.org", True)
    assert re.search(r'name="ticket_mails"[^>]*checked', client.get("/admin/profile").text)
