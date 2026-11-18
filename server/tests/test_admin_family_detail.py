"""Verwaltungsbereich 2, Etappe 3: Detailansicht – Karten, Bearbeiten je Karte, Nutzung, Support, Verlauf."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import web
from app.config import Settings
from app.models import AdminLog, Child, Device, Family, utcnow
from conftest import SessionLocal, csrf_from, platform_db
from test_admin_roles import _login, _operator


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _inputs(html):
    """Eingabefelder der bearbeitbaren Karten (ohne CSRF und ohne die Aktion „Rechnung erstellen“)."""
    return [n for n in re.findall(r"<(?:input|select)\b[^>]*name=\"(?!csrf_token)([a-z_]+)\"", html) if n != "price_item"]


def test_cards_without_edit_have_no_inputs(client, saas):
    with SessionLocal() as db:
        db.add_all([Child(name="Geheimkind"), Device(name="Geheimgerät", token_hash="g" * 64, platform="windows",
                                                     agent_version="0.33.0", last_seen_at=utcnow())])
        db.commit()
    _login(client, _operator("admin"))
    page = client.get("/admin/families/1").text
    for card in ("kopf", "abo", "rechnungsangaben", "nutzung", "support", "rechnungen", "verlauf"):
        assert f'id="{card}"' in page
    assert _inputs(page) == [] and 'name="price_item"' in page             # nur Werte; Aktion: Rechnung erstellen
    assert "Windows" in page and "0.33.0" in page and "gerade eben" in page
    assert "Geheimkind" not in page and "Geheimgerät" not in page         # Nutzung: nur Metadaten
    name_form = client.get("/admin/families/1?edit=name").text
    assert _inputs(name_form) == ["name"] and 'href="/admin/families/1#kopf"' in name_form   # Abbrechen
    abo_form = client.get("/admin/families/1?edit=abo").text
    assert set(_inputs(abo_form)) == {"plan", "status", "trial_ends", "paid_until", "provider", "customer"}
    assert _inputs(client.get("/admin/families/1?edit=unsinn").text) == []


def test_save_per_card_and_history(client, saas, monkeypatch):
    from app import policy
    pushed = []

    async def fake(db, ids):
        pushed.append(set(ids))
    monkeypatch.setattr(policy, "push_policies", fake)
    with SessionLocal() as db:
        db.add(Device(name="PC", token_hash="p" * 64))
        db.commit()
    with platform_db() as db:
        f = db.get(Family, 1)
        f.plan, f.billing_status, f.trial_ends_at, f.paused_at = "standard", "trial", utcnow() - timedelta(days=30), utcnow()
        db.commit()
    _login(client, _operator("admin"))
    tok = csrf_from(client.get("/admin/families/1?edit=name").text)
    r = client.post("/admin/families/1/name", data={"csrf_token": tok, "name": "Familie Detail"}, follow_redirects=False)
    assert r.headers["location"].endswith("#kopf")
    r = client.post("/admin/families/1", data={"csrf_token": tok, "plan": "standard", "status": "active",
                                               "paid_until": (utcnow() + timedelta(days=30)).date().isoformat()},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#abo")
    with platform_db() as db:
        assert db.get(Family, 1).paused_at is None                         # wirkt sofort
        fields = {e.field for e in db.scalars(select(AdminLog).where(AdminLog.family_id == 1))}
    assert {"name", "billing_status", "paid_until", "paused_at"} <= fields and pushed
    page = client.get("/admin/families/1?saved=1").text
    verlauf = page[page.index('id="verlauf"'):]
    assert "Familie Detail" in verlauf and "billing_status" in verlauf and "System" in verlauf
    bad = client.post("/admin/families/1", data={"csrf_token": tok, "plan": "gold", "status": "active"})
    assert bad.status_code == 400 and 'name="plan"' in bad.text                 # Fehler: Formular bleibt offen


def test_support_role_cannot_edit(client, saas):
    _login(client, _operator("support"))
    for card in ("name", "abo"):
        page = client.get(f"/admin/families/1?edit={card}").text
        assert _inputs(page) == [] and "?edit=" not in page
    tok = csrf_from(client.get("/admin/profile").text)
    assert client.post("/admin/families/1/name", data={"csrf_token": tok, "name": "X"}).status_code == 403


def test_support_card_shows_grant_and_accesses(client, parent, saas):
    from conftest import login
    login(client, parent)
    tok = csrf_from(client.get("/settings").text)
    client.post("/settings/support", data={"csrf_token": tok, "hours": 24})
    client.cookies.clear()
    _login(client, _operator("support"))
    page = client.get("/admin/families/1").text
    assert 'id="rechnungen"' not in page                                   # Support: keine Rechnungen
    support = page[page.index('id="support"'):page.index('id="verlauf"')]
    assert "freigegeben bis" in support and 'action="/admin/support/1"' in support
    tok = csrf_from(page)
    client.post("/admin/support/1", data={"csrf_token": tok})
    client.cookies.delete("kidctl_support")
    support = client.get("/admin/families/1").text
    assert "Zugriffsprotokoll" in support and "op-support" in support
