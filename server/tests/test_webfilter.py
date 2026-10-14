"""Website-Filter (Server): Domains, Listenformate, Einstellungen -> Policy, Listen-Download/-Abruf."""

import asyncio

import httpx
import pytest
from sqlalchemy import select

from kidctl_common import protocol as p

from app import webfilter
from app.db import SessionLocal
from app.models import BlocklistState, Child

from conftest import recv
from test_policy import _policy, _setup, device


def test_normalize_domain():
    n = webfilter.normalize_domain
    assert n("https://Www.Example.COM/pfad") == "www.example.com"
    assert n("*.schule.de") == "schule.de"
    assert n("bücher.de") == "xn--bcher-kva.de"
    assert n("localhost") is None and n("-x.de") is None and n("a..b") is None and n("") is None


def test_parse_domains_formats():
    text = """# Kommentar
! adblock-kommentar
0.0.0.0 ads.example.com
127.0.0.1 localhost
tracker.example.net
||adblock.example.org^
*.wild.example.com
ungültig_domain
"""
    assert webfilter.parse_domains(text) == ["adblock.example.org", "ads.example.com",
                                             "tracker.example.net", "wild.example.com"]


def checked_lists(html: str) -> set[str]:
    import re
    return set(re.findall(r'name="list" value="([^"]+)"[^>]*\bchecked', html, re.S))


def _save(client, cid, tok, **form):
    data = {"csrf_token": tok, "web_mode": "off", "allow": "", "deny": "", **form}
    return client.post(f"/children/{cid}/web", data=data, follow_redirects=False)


def test_settings_to_policy(client, parent, monkeypatch):
    monkeypatch.setattr(webfilter, "refresh_lists", lambda only=None: asyncio.sleep(0, set()))
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        assert _policy(ws).children[0].web.mode == "off"
        # Vorauswahl HaGeZi Multi + NSFW, solange keine Liste gewählt ist
        assert checked_lists(client.get(f"/children/{cid}").text) == {"hagezi-multi", "hagezi-nsfw"}

        r = _save(client, cid, tok, web_mode="whitelist", safesearch="1", bundle=["wikipedia", "youtube", "x"],
                  allow="schule.de\nhttps://Lernen.example.org/x")
        assert r.status_code == 303
        web = _policy(ws).children[0].web
        assert web.mode == "whitelist" and web.safesearch is True
        assert {"schule.de", "lernen.example.org", "wikipedia.org", "googlevideo.com"} <= set(web.allow)
        assert web.deny == [] and web.lists == []

        r = _save(client, cid, tok, web_mode="blacklist", list=["hagezi-multi", "hagezi-nsfw", "boese"],
                  deny="tiktok.com", allow="ausnahme.de")
        web = _policy(ws).children[0].web
        assert web.mode == "blacklist" and web.safesearch is False
        assert web.deny == ["tiktok.com"] and web.allow == ["ausnahme.de"]
        assert [(x.id, x.sha256) for x in web.lists] == [("hagezi-multi", ""), ("hagezi-nsfw", "")]

        r = _save(client, cid, tok, web_mode="blacklist", deny="kein domain!")
        assert r.status_code == 400 and "Ungültige Domain" in r.text
        assert _save(client, cid, tok, web_mode="kaputt").status_code == 400


LIST_BODY = "0.0.0.0 ads.example.com\n0.0.0.0 track.example.net\n"


@pytest.fixture
def fake_lists(monkeypatch):
    """httpx-Client mit simuliertem Server statt echtem Download."""
    def handler(request):
        return httpx.Response(200, text=LIST_BODY)
    real = httpx.AsyncClient
    monkeypatch.setattr(webfilter.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_list_fetch_download_and_push(client, parent, fake_lists):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        _policy(ws)
        _save(client, cid, tok, web_mode="blacklist", list=["stevenblack"])
        assert _policy(ws).children[0].web.lists[0].sha256 == ""
        # Hintergrund-Download (in der Route angestoßen) -> neue Policy mit Version
        web = _policy(ws).children[0].web
        sha = web.lists[0].sha256
        assert len(sha) == 64
        with SessionLocal() as db:
            st = db.get(BlocklistState, "stevenblack")
            assert st.count == 2 and st.error == ""

    assert client.get(p.LISTS_PATH + "/stevenblack").status_code == 401  # ohne Geräte-Token


def test_list_endpoint_with_token(client, parent, fake_lists):
    cid, tok = _setup(client, parent)
    r = client.post("/devices/pair", data={"name": "pc", "csrf_token": tok})
    code = r.text.split("sudo kidctl enroll http://testserver ")[1][:8]
    token = client.post(p.ENROLL_PATH, json={"code": code, "hostname": "pc"}).json()["token"]
    asyncio.run(webfilter.refresh_lists({"stevenblack"}))
    h = {"Authorization": f"Bearer {token}"}
    r = client.get(p.LISTS_PATH + "/stevenblack", headers=h)
    assert r.status_code == 200 and r.text == "ads.example.com\ntrack.example.net\n"
    etag = r.headers["etag"]
    assert client.get(p.LISTS_PATH + "/stevenblack", headers={**h, "If-None-Match": etag}).status_code == 304
    assert client.get(p.LISTS_PATH + "/gibts-nicht", headers=h).status_code == 404
    assert client.get(p.LISTS_PATH + "/stevenblack", headers={"Authorization": "Bearer falsch"}).status_code == 401


def test_new_child_and_migration_default_off(client, parent):
    cid, _ = _setup(client, parent)
    with SessionLocal() as db:
        c = db.get(Child, cid)
        assert c.web_mode == "off" and c.safesearch is True
