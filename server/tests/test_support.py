"""Etappe 4.4: Support-Freigabe – zeitlich begrenzt, widerrufbar, nur lesend, protokolliert."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import web
from app.config import Settings
from app.models import Child, SupportAccess, SupportGrant, utcnow
from conftest import SessionLocal, add_family, add_parent, csrf_from, login, platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


def _grant(client, hours=24):
    tok = csrf_from(client.get("/settings").text)
    return client.post("/settings/support", data={"csrf_token": tok, "hours": hours}, follow_redirects=False)


def _open(client, family_id=1):
    tok = csrf_from(client.get("/admin").text)
    return client.post(f"/admin/support/{family_id}", data={"csrf_token": tok}, follow_redirects=False)


def test_owner_grants_operator_reads_only(client, parent, operator, saas):  # noqa: F811
    with SessionLocal() as db:
        db.add(Child(name="Lena"))
        db.commit()
    login(client, parent)
    assert "Support-Zugriff freigeben" in client.get("/settings").text
    assert _grant(client).status_code == 303
    assert "freigegeben bis" in client.get("/settings").text
    client.cookies.clear()

    admin_login(client, operator)
    assert "/admin/support/1" in client.get("/admin/families/1").text
    assert "Support-Freigabe aktiv" in client.get("/admin").text and "/admin/families/1" in client.get("/admin?issue=support").text
    r = _open(client)
    assert r.status_code == 303 and "kidctl_support" in r.headers["set-cookie"]
    page = client.get("/children").text
    assert "Lena" in page and "Support-Ansicht (nur lesend)" in page and 'action="/logout"' not in page
    assert client.get("/profile").status_code == 403                                   # persönlich
    with SessionLocal() as db:
        child_id = db.scalar(select(Child.id))
    r = client.post(f"/children/{child_id}/chat", data={"csrf_token": "", "body": "hallo"})
    assert r.status_code == 403                                                        # nichts senden
    r = client.post("/children", data={"csrf_token": "", "name": "X"})
    assert r.status_code == 403                                                        # nichts ändern
    with SessionLocal() as db:
        paths = [a.path for a in db.scalars(select(SupportAccess).order_by(SupportAccess.id))]
        assert paths[0] == "(geöffnet)" and "/children" in paths and all(a.operator == "betrieb"
                                                                        for a in db.scalars(select(SupportAccess)))
        assert db.scalar(select(Child.id).where(Child.name == "X")) is None
    r = client.get("/admin/support/end", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/children", follow_redirects=False).headers["location"] == "/login"


def test_revoke_ends_open_view_and_parent_sees_log(client, parent, operator, saas):  # noqa: F811
    papa = add_parent(username="papa")
    login(client, parent)
    _grant(client, 1)
    client.cookies.clear()
    admin_login(client, operator)
    _open(client)
    assert client.get("/children").status_code == 200
    support_cookies = dict(client.cookies)

    client.cookies.clear()
    login(client, papa)                                                                 # Elternteil, nicht Inhaber
    page = client.get("/settings").text
    assert "Freigabe beenden" in page and "Protokoll der Support-Zugriffe" in page and "/children" in page
    tok = csrf_from(page)
    assert client.post("/settings/support", data={"csrf_token": tok, "hours": 24}).status_code == 403
    assert client.post("/settings/support/revoke", data={"csrf_token": tok}, follow_redirects=False).status_code == 303

    client.cookies.clear()
    client.cookies.update(support_cookies)
    assert client.get("/children", follow_redirects=False).headers["location"] == "/login"   # sofort zu
    assert _open(client).status_code == 403                                                  # keine Freigabe mehr


def test_grant_expires_and_other_family_not_visible(client, parent, operator, saas):  # noqa: F811
    other = add_family("Andere")
    login(client, parent)
    _grant(client, 1)
    assert _grant(client, 5).status_code == 400                                         # nur 1/24/72 h
    client.cookies.clear()
    admin_login(client, operator)
    assert _open(client, other).status_code == 403                                      # ohne Freigabe
    _open(client)
    with platform_db() as db:
        for g in db.scalars(select(SupportGrant)):
            g.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_operator_logout_ends_view(client, parent, operator, saas):  # noqa: F811
    login(client, parent)
    _grant(client)
    client.cookies.clear()
    admin_login(client, operator)
    _open(client)
    support_cookie = client.cookies.get("kidctl_support")
    tok = csrf_from(client.get("/admin").text)
    client.post("/admin/logout", data={"csrf_token": tok})
    client.cookies.set("kidctl_support", support_cookie)
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_single_mode_has_no_support_section(client, parent):
    login(client, parent)
    assert "Support-Zugriff" not in client.get("/settings").text
    tok = csrf_from(client.get("/settings").text)
    assert client.post("/settings/support", data={"csrf_token": tok, "hours": 24}).status_code == 404
