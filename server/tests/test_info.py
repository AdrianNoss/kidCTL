"""Info zur laufenden Version: Abschnitt in den Einstellungen, /admin/info (beide Rollen), Hinweis „Neu in Version …“
auf der Übersicht, Prüfung auf eine neuere Version."""

import asyncio
import re
from pathlib import Path

import httpx
import pytest

from conftest import SessionLocal, csrf_from, login
from test_admin_roles import _login, _operator, saas  # noqa: F401 (Fixture)

from app import about
from app.models import Parent
from app.release_notes import RELEASES


def test_version_matches_agent():
    """Der oberste Eintrag der Release Notes ist die Version, die auch der Agent trägt."""
    init = (Path(__file__).resolve().parents[2] / "agent" / "kidctl_agent" / "__init__.py").read_text()
    assert about.VERSION == re.search(r'__version__ = "([^"]+)"', init).group(1)
    assert all(e["de"] and e["en"] for e in RELEASES)
    assert [about.version_key(e["version"]) for e in RELEASES] == sorted(
        (about.version_key(e["version"]) for e in RELEASES), reverse=True)


def test_info_requires_login(client):
    assert client.get("/settings", follow_redirects=False).status_code == 303
    assert client.get("/admin/info", follow_redirects=False).status_code in (303, 401, 403, 404)
    assert client.post("/info/seen", follow_redirects=False).status_code in (303, 403)


def test_settings_show_info(client, parent):
    login(client, parent)
    page = client.get("/settings").text
    assert 'id="info"' in page and f"KidCTL {about.VERSION}" in page
    for url in (about.HOMEPAGE, about.RELEASES_URL, about.DOCS_URL, about.SECURITY_EMAIL, "AGPL-3.0"):
        assert url in page
    assert RELEASES[0]["de"][0] in page
    assert "basiert auf KidCTL" not in page


def test_whitelabel_names_kidctl(client, parent, monkeypatch):
    from app import branding, web
    monkeypatch.setitem(web.templates.env.globals, "brand", lambda: branding.Brand(name="Familienschutz", custom=True))
    login(client, parent)
    assert "Familienschutz basiert auf KidCTL" in client.get("/settings").text


@pytest.mark.parametrize("role", ["admin", "support"])
def test_admin_info_both_roles(client, saas, role):  # noqa: F811
    _login(client, _operator(role))
    page = client.get("/admin/info")
    assert page.status_code == 200 and f"KidCTL {about.VERSION}" in page.text


def test_banner_after_update_until_confirmed(client, parent):
    with SessionLocal() as db:   # Stand vor dem Update
        db.get(Parent, parent.id).seen_version = "0.1.0"
        db.commit()
    login(client, parent)
    home = client.get("/").text
    assert f"Neu in Version {about.VERSION}" in home and RELEASES[0]["de"][0] in home
    r = client.post("/info/seen", data={"csrf_token": csrf_from(home)}, follow_redirects=False)
    assert r.status_code == 303
    assert "Neu in Version" not in client.get("/").text
    with SessionLocal() as db:
        assert db.get(Parent, parent.id).seen_version == about.VERSION


def test_new_parent_sees_no_banner(client, parent):
    assert parent.seen_version == about.VERSION
    login(client, parent)
    assert "Neu in Version" not in client.get("/").text


def test_banner_english(client, parent):
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        p.seen_version, p.language = "0.1.0", "en"
        db.commit()
    login(client, parent)
    home = client.get("/").text
    assert f"New in version {about.VERSION}" in home and RELEASES[0]["en"][0] in home


def test_banner_csrf(client, parent):
    login(client, parent)
    client.get("/")
    assert client.post("/info/seen", data={"csrf_token": "falsch"}, follow_redirects=False).status_code == 403


def test_newer_version_hint(client, parent, monkeypatch):
    def handler(request):
        assert request.url == about.VERSION_URL and "cookie" not in request.headers
        return httpx.Response(200, json={"version": "v99.0.0"})
    monkeypatch.setattr(about, "_latest", None)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await about.check_latest(c)
    assert asyncio.run(run()) == "99.0.0"
    login(client, parent)
    assert "Version 99.0.0 ist verfügbar." in client.get("/settings").text


def test_version_check_errors_keep_state(monkeypatch):
    monkeypatch.setattr(about, "_latest", "1.0.0")
    async def run(resp):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: resp)) as c:
            return await about.check_latest(c)
    assert asyncio.run(run(httpx.Response(500))) == "1.0.0"
    assert asyncio.run(run(httpx.Response(200, text="kein json"))) == "1.0.0"
    assert asyncio.run(run(httpx.Response(200, json={"version": "<script>"}))) == "1.0.0"
    assert about.info()["newer"] is None or about.version_key("1.0.0") > about.version_key(about.VERSION)
