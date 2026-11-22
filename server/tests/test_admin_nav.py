"""Verwaltungsbereich mit derselben Navigation wie die Elternoberfläche: je Rolle alle Bereiche erreichbar, aktive
Markierung, am Handy drei Einträge + „Mehr“, keine Reiter mehr."""

import re

import pytest

from test_admin_roles import _login, _operator, saas  # noqa: F401 (Fixture)

ADMIN = ["/admin", "/admin/tickets", "/admin/invoices", "/admin/codes", "/admin/newsletter", "/admin/mail-preview",
         "/admin/branding", "/admin/log", "/admin/info"]
SUPPORT = ["/admin", "/admin/tickets", "/admin/log", "/admin/info"]


def _nav(html):
    nav = re.search(r'<nav class="navbar admin-nav".*?</nav>', html, re.S).group(0)
    return re.findall(r'<a href="([^"]+)" class="nav-item([^"]*)"', nav)


@pytest.mark.parametrize("role,expected,mobile", [
    ("admin", ADMIN, ["/admin", "/admin/tickets", "/admin/invoices"]),
    ("support", SUPPORT, ["/admin", "/admin/tickets", "/admin/log"]),
])
def test_nav_per_role(client, saas, role, expected, mobile):  # noqa: F811
    _login(client, _operator(role))
    for path in expected:
        page = client.get(path)
        assert page.status_code == 200, path
        items = _nav(page.text)
        assert [h for h, _c in items] == expected + ["/admin/more"], path
        assert [h for h, c in items if "nav-rail-only" not in c] == mobile + ["/admin/more"]   # Handy: 3 + Mehr
        active = [h for h, c in items if "active" in c]
        assert active == [path], path
        assert 'class="tabs"' not in page.text and 'action="/admin/logout"' in page.text
    more = client.get("/admin/more").text
    rest = [h for h in expected if h not in mobile]
    for href in rest + ["/admin/profile"]:
        assert f'href="{href}" class="more-item"' in more
    assert [h for h, c in _nav(more) if "active" in c] == ["/admin/more"]
    if role == "support":
        assert client.get("/admin/codes", follow_redirects=False).status_code == 403      # Rechte bleiben serverseitig


def test_family_detail_marks_families(client, saas):  # noqa: F811
    from conftest import add_family
    _login(client, _operator("admin"))
    fid = add_family("Test")
    page = client.get(f"/admin/families/{fid}").text
    assert [h for h, c in _nav(page) if "active" in c] == ["/admin"]


def test_parent_pages_keep_parent_nav(client, parent):
    from conftest import login
    login(client, parent)
    page = client.get("/").text
    assert "admin-nav" not in page and 'href="/children"' in page
