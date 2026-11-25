"""Limit je Programm/Kategorie (K4, Server): Einstellung im WebUI, Policy, Zeit je Limit geräteübergreifend,
Zusammenspiel mit „Zählt nicht“, Anzeige unter „Heute“."""

import html
from contextlib import ExitStack

from sqlalchemy import select

from kidctl_common import protocol as p

from conftest import SessionLocal, recv
from app.models import ChildAppRule, Device
from app.schedule import today_local

from test_free_apps import APPS, _save
from test_policy import _policy, _setup, _usage, device
from test_programs import _sync


def _last(ws, kind):
    """Letzte Nachricht ``kind`` bis zum Pong (vorher gepushte Policies/Nutzungsstände überspringen)."""
    ws.send_text(p.Ping().model_dump_json())
    last = None
    while not isinstance(m := recv(ws, keep_policy=True), p.Pong):
        if isinstance(m, kind):
            last = m
    return last


def _pol(ws) -> p.ChildPolicy:
    return _last(ws, p.Policy).children[0]


def _upd(ws_from, ws_to) -> p.UsageUpdate:
    _sync(ws_from)
    return _last(ws_to, p.UsageUpdate)


def _limit(client, cid, tok, key, minutes):
    return client.post(f"/children/{cid}/limits", data={"csrf_token": tok, "key": key, "minutes": str(minutes)},
                       follow_redirects=False)


def _remove(client, cid, tok, key):
    return client.post(f"/children/{cid}/limits/remove", data={"csrf_token": tok, "key": key},
                       follow_redirects=False)


def test_limits_set_change_remove(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        assert _pol(ws).app_limits == []
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        page = client.get(f"/children/{cid}/zeiten").text
        assert "Limits für Programme" in page and 'value="cat:browser"' in page
        assert 'value="exe:/usr/bin/gcompris-qt"' in page

        assert _limit(client, cid, tok, "cat:browser", 60).status_code == 303
        assert _limit(client, cid, tok, "exe:/usr/bin/gcompris-qt", 30).status_code == 303
        limits = _pol(ws).app_limits
        assert [(lim.key, lim.minutes, lim.name) for lim in limits] == [
            ("cat:browser", 60, ""), ("exe:/usr/bin/gcompris-qt", 30, "GCompris")]

        # Ändern, ungültige Eingaben ohne Teilspeicherung
        _limit(client, cid, tok, "cat:browser", 45)
        assert _pol(ws).app_limits[0].minutes == 45
        for key, minutes in (("cat:boese", 10), ("exe:/usr/bin/unbekannt", 10), ("cat:chat", -1),
                             ("cat:chat", 2000), ("cat:chat", "x")):
            r = _limit(client, cid, tok, key, minutes)
            assert r.status_code == 400, (key, minutes)
        with SessionLocal() as db:
            assert len(db.scalars(select(ChildAppRule)).all()) == 2

        # Gerät ohne Fähigkeit -> Hinweis
        page = client.get(f"/children/{cid}/zeiten").text
        assert "kennt Limits für Programme noch nicht" in page
        with SessionLocal() as db:
            db.get(Device, dev_id).features = f"{p.FEATURE_FREE_APPS},{p.FEATURE_APP_LIMITS}"
            db.commit()
        page = client.get(f"/children/{cid}/zeiten").text
        assert "kennt Limits für Programme noch nicht" not in page
        assert 'value="cat:browser"' not in page.split("Limit hinzufügen")[0].split("<select")[-1]   # schon gesetzt

        _remove(client, cid, tok, "cat:browser")
        _remove(client, cid, tok, "exe:/usr/bin/gcompris-qt")
        assert _pol(ws).app_limits == []
        with SessionLocal() as db:
            assert db.scalars(select(ChildAppRule)).all() == []


def test_limit_and_free_share_rule(client, parent):
    """„Zählt nicht“ und Limit in derselben Zeile: Abwahl des einen lässt das andere bestehen."""
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _dev):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        key = "exe:/usr/bin/gcompris-qt"
        _save(client, cid, tok, free=[key])
        _limit(client, cid, tok, key, 20)
        pol = _pol(ws)
        assert pol.free_apps == [key] and pol.app_limits[0].key == key
        _save(client, cid, tok, free=[])    # „zählt nicht“ aus -> Limit bleibt
        pol = _pol(ws)
        assert pol.free_apps == [] and [lim.key for lim in pol.app_limits] == [key]
        _save(client, cid, tok, free=[key])
        _remove(client, cid, tok, key)       # Limit weg -> „zählt nicht“ bleibt
        pol = _pol(ws)
        assert pol.free_apps == [key] and pol.app_limits == []


def test_max_limits(client, parent):
    cid, tok = _setup(client, parent)
    apps = [p.AppEntry(key=f"exe:/usr/bin/app{i}", name=f"App {i}") for i in range(25)]
    with device(client, tok, "pc", map_to=cid) as (ws, _dev):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=apps).model_dump_json())
        _sync(ws)
        for i in range(20):
            assert _limit(client, cid, tok, f"exe:/usr/bin/app{i}", 10).status_code == 303
        assert _limit(client, cid, tok, "exe:/usr/bin/app20", 10).status_code == 400
        assert _limit(client, cid, tok, "exe:/usr/bin/app3", 15).status_code == 303   # Ändern geht weiter
        assert "Limit hinzufügen" not in client.get(f"/children/{cid}/zeiten").text


def test_limit_usage_across_devices(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local()
    with ExitStack() as stack:
        ws_a, _a = stack.enter_context(device(client, tok, "laptop", map_to=cid))
        ws_b, _b = stack.enter_context(device(client, tok, "desktop", map_to=cid))
        for ws in (ws_a, ws_b):
            _policy(ws), _usage(ws)
        ws_a.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws_a)
        _limit(client, cid, tok, "cat:browser", 60)
        for ws in (ws_a, ws_b):
            _policy(ws)

        ws_a.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=900,
                                                           limit_seconds={"cat:browser": 600})]).model_dump_json())
        upd = _upd(ws_a, ws_b)
        assert upd.others_limit_seconds == {"cat:browser": 600}
        # kleinerer Stand ändert nichts, nur Limit-Zeit gewachsen -> trotzdem weitergemeldet
        ws_a.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=900,
                                                           limit_seconds={"cat:browser": 300})]).model_dump_json())
        ws_a.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=900,
                                                           limit_seconds={"cat:browser": 1200})]).model_dump_json())
        assert _upd(ws_a, ws_b).others_limit_seconds == {"cat:browser": 1200}
        ws_b.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=300,
                                                           limit_seconds={"cat:browser": 180})]).model_dump_json())
        assert _upd(ws_b, ws_a).others_limit_seconds == {"cat:browser": 180}

        # Heute: 20 + 3 = 23 min von 1 h
        page = html.unescape(client.get(f"/children/{cid}").text)
        facts = page.split("today-facts")[1].split("</dl>")[0]
        assert "<dt>Browser</dt><dd>0:23 h von 1:00 h</dd>" in facts, facts


def test_old_agent_report_without_limits(client, parent):
    """Ältere Agenten melden kein ``limit_seconds`` – die Nutzung wird normal übernommen."""
    cid, tok = _setup(client, parent)
    today = today_local()
    with ExitStack() as stack:
        ws_a, _a = stack.enter_context(device(client, tok, "laptop", map_to=cid))
        ws_b, _b = stack.enter_context(device(client, tok, "desktop", map_to=cid))
        for ws in (ws_a, ws_b):
            _policy(ws), _usage(ws)
        ws_a.send_text('{"type": "usage_report", "entries": [{"username": "max", "day": "%s", "seconds": 120}]}'
                       % today.isoformat())
        upd = _upd(ws_a, ws_b)
        assert upd.others_seconds == 120 and upd.others_limit_seconds == {}


def test_english_card(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _dev):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=APPS).model_dump_json())
        _sync(ws)
        _limit(client, cid, tok, "cat:media", 30)
        client.get(f"/lang/en?next=/children/{cid}/zeiten")
        page = html.unescape(client.get(f"/children/{cid}/zeiten").text)
        assert "Program limits" in page and "Music & video" in page and "Add limit" in page
        assert _pol(ws).app_limits[0].name == ""   # Kategorie: Name übersetzt der Agent
