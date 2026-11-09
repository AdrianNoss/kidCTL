"""Seiten eines Kindes: Reiter, Stand heute, Weiterleitungen nach dem Speichern."""

from __future__ import annotations

from kidctl_common import protocol as p

from app import programs
from app.db import SessionLocal
from test_policy import _policy, _setup, device
from test_programs import _sync

TABS = {"": "Heute", "/zeiten": "Zeiten", "/internet": "Internet", "/programme": "Programme", "/stats": "Statistik",
        "/protokoll": "Protokoll", "/chat": "Chat"}


def test_every_tab_shows_the_tab_bar_and_marks_itself(client, parent):
    cid, _tok = _setup(client, parent)
    for path, label in TABS.items():
        page = client.get(f"/children/{cid}{path}").text
        assert f'aria-current="page">{label}' in page, path
        assert all(f'href="/children/{cid}{p}"' in page for p in TABS), path


def test_today_shows_status_and_settings_summary(client, parent):
    cid, tok = _setup(client, parent)
    page = client.get(f"/children/{cid}").text
    assert "1:00 h" in page and "heute noch übrig" in page and "kein Rechner" in page
    assert "Jetzt sperren" in page and "Gutschreiben" in page
    assert "Wochenvorlage" not in page and "Sperrlisten" not in page   # Einstellungen liegen auf eigenen Reitern
    client.post(f"/children/{cid}/lock", data={"csrf_token": tok, "duration": ""})
    assert 'today-value">Gesperrt' in client.get(f"/children/{cid}").text


def test_saving_returns_to_the_same_tab(client, parent):
    cid, tok = _setup(client, parent)
    day = {"csrf_token": tok, "quota_wd": "90", "start_wd": "14:00", "end_wd": "19:30", "quota_we": "90",
           "start_we": "09:00", "end_we": "21:00"}
    posts = [("schedule", day, f"/children/{cid}/zeiten?saved=1"),
             ("web", {"csrf_token": tok, "web_mode": "off"}, f"/children/{cid}/internet?web=1"),
             ("programs", {"csrf_token": tok, "program_mode": "off"}, f"/children/{cid}/programme?programs=1")]
    for action, data, target in posts:
        r = client.post(f"/children/{cid}/{action}", data=data, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == target, action
    r = client.post(f"/children/{cid}/schedule", data=day | {"quota_wd": "abc"})
    assert r.status_code == 400 and 'aria-current="page">Zeiten' in r.text   # Fehler bleibt auf dem Reiter


def test_program_page_only_explains_the_childs_platforms(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        _policy(ws)
    page = client.get(f"/children/{cid}/programme").text
    assert "KDE/Plasma" in page and "Windows-Bestandteile" not in page and "/usr/bin/git" in page


def test_same_name_entries_can_be_told_apart(client, parent):
    cid, tok = _setup(client, parent)
    apps = [p.AppEntry(key="appx:Microsoft.GamingApp_8wekyb3d8bbwe", name="Xbox", categories=["Game"]),
            p.AppEntry(key="appx:Microsoft.XboxApp_8wekyb3d8bbwe", name="Xbox", categories=["Game"]),
            p.AppEntry(key="exe:/usr/bin/foo", name="Foo", categories=["Game"])]
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        _policy(ws)
        ws.send_text(p.ReportApps(apps=apps).model_dump_json())
        _sync(ws)
    with SessionLocal() as db:
        rows = programs.grouped_catalog(programs.catalog_for_child(db, cid))[0][2]
    assert [(e.name, e.note) for e in rows] == [("Foo", ""), ("Xbox", "Microsoft.GamingApp"), ("Xbox", "Microsoft.XboxApp")]
