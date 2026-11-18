"""Familienname und Rechnungsangaben (von der Familie hinterlegt, für den Betreiber lesbar)."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app import web
from app.config import Settings
from app.db import engine
from app.models import BillingAddress, Family, utcnow
from conftest import SessionLocal, add_parent, csrf_from, login, platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)

ADDRESS = {"name": "Maria Muster", "street": "Hauptstraße 1", "postal_code": "12345", "city": "Musterstadt",
           "country": "Deutschland", "email": "rechnung@example.org", "vat_id": ""}


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _family(**kw):
    with platform_db() as db:
        f = db.get(Family, 1)
        for k, v in kw.items():
            setattr(f, k, v)
        db.commit()


def test_owner_names_family_encrypted(client, parent, operator):  # noqa: F811
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    r = client.post("/settings/family", data={"csrf_token": tok, "name": "Familie Muster"}, follow_redirects=False)
    assert r.status_code == 303
    with engine.connect() as c:
        raw = c.execute(text("SELECT name FROM family WHERE id = 1")).scalar()
    assert raw.startswith("p1:") and "Muster" not in raw                       # verschlüsselt gespeichert
    assert 'value="Familie Muster"' in client.get("/settings").text
    client.cookies.clear()
    admin_login(client, operator)
    assert "Familie Muster" in client.get("/admin").text                         # Betreiber kann lesen
    tok = csrf_from(client.get("/admin/families/1").text)
    client.post("/admin/families/1/name", data={"csrf_token": tok, "name": "Muster (Freunde)"})
    with platform_db() as db:
        assert db.get(Family, 1).name == "Muster (Freunde)"


def test_only_owner_renames(client, parent):
    papa = add_parent(username="papa")
    login(client, papa)
    tok = csrf_from(client.get("/settings").text)
    assert client.post("/settings/family", data={"csrf_token": tok, "name": "X"}).status_code == 403


def test_billing_address_required_and_banner(client, parent, operator, saas):  # noqa: F811
    _family(plan="standard", billing_status="trial", trial_ends_at=utcnow() + timedelta(days=20))
    login(client, parent)
    assert "Rechnungsangaben" in client.get("/settings").text
    assert "Bitte hinterlegt eure Rechnungsangaben" not in client.get("/").text  # Testphase noch lang
    _family(billing_status="active", paid_until=utcnow() + timedelta(days=300))
    assert "Bitte hinterlegt eure Rechnungsangaben" in client.get("/").text      # aktiv ohne Angaben

    tok = csrf_from(client.get("/settings").text)
    r = client.post("/settings/billing", data={"csrf_token": tok, **ADDRESS, "city": ""})
    assert r.status_code == 400 and "Pflichtfelder" in r.text and 'value="Hauptstraße 1"' in r.text
    r = client.post("/settings/billing", data={"csrf_token": tok, **ADDRESS, "email": "kaputt"})
    assert r.status_code == 400
    r = client.post("/settings/billing", data={"csrf_token": tok, **ADDRESS}, follow_redirects=False)
    assert r.status_code == 303
    assert "Bitte hinterlegt eure Rechnungsangaben" not in client.get("/").text
    with engine.connect() as c:
        raw = c.execute(text("SELECT street, email FROM billing_address")).first()
    assert all(v.startswith("p1:") for v in raw) and "Hauptstraße" not in raw[0]
    with SessionLocal() as db:
        assert db.scalar(select(BillingAddress)).complete

    client.cookies.clear()
    admin_login(client, operator)
    page = client.get("/admin/families/1").text
    assert "Hauptstraße 1" in page and "rechnung@example.org" in page
    assert 'title="Rechnungsangaben fehlen"' not in client.get("/admin").text


def test_missing_address_shows_in_admin(client, parent, operator, saas):  # noqa: F811
    _family(plan="standard", billing_status="active")
    admin_login(client, operator)
    assert 'title="Rechnungsangaben fehlen"' in client.get("/admin").text


def test_billing_address_saas_only_and_owner_only(client, parent):
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    assert client.post("/settings/billing", data={"csrf_token": tok, **ADDRESS}).status_code == 404


def test_family_deletion_removes_address(client, parent, saas):
    from app.tenant import delete_family
    with SessionLocal() as db:
        db.add(BillingAddress(**{k: v for k, v in ADDRESS.items()}))
        db.commit()
    with platform_db() as db:
        delete_family(db, 1)
        assert db.scalar(select(BillingAddress)) is None
