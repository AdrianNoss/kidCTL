"""Sperrzeiten und Fenster über Mitternacht: Formular, Policy v1/v2, Gutschrift, Hinweis für alte Agenten."""

from datetime import time

from sqlalchemy import select

from kidctl_common import protocol as p

from conftest import SessionLocal, csrf_from, login
from app.models import Child
from app.schedule import today_local
from test_policy import _policy, device

BASE = {"quota_wd": "120", "start_wd": "07:00", "end_wd": "20:00",
        "quota_we": "180", "start_we": "09:00", "end_we": "21:00"}


def _setup(client, parent):
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    with SessionLocal() as db:
        return db.scalar(select(Child.id)), tok


def _save(client, cid, tok, **extra):
    return client.post(f"/children/{cid}/schedule", data={"csrf_token": tok, **BASE, **extra},
                       follow_redirects=False)


def _blocks(cid, weekday):
    with SessionLocal() as db:
        rule = next(r for r in db.get(Child, cid).rules if r.weekday == weekday)
        return [(b.start, b.end, b.name) for b in rule.block_list]


def test_blocks_saved_for_group_and_shown(client, parent):
    cid, tok = _setup(client, parent)
    r = _save(client, cid, tok, block_wd_0_start="08:00", block_wd_0_end="13:00", block_wd_0_name="Schule",
              block_wd_1_start="18:00", block_wd_1_end="19:00")
    assert r.status_code == 303
    assert all(_blocks(cid, d) == [(time(8), time(13), "Schule"), (time(18), time(19), "")] for d in range(5))
    assert _blocks(cid, 5) == []
    page = client.get(f"/children/{cid}/zeiten").text
    assert "Sperrzeiten" in page and 'value="Schule"' in page
    # Leeren entfernt die Sperrzeit
    assert _save(client, cid, tok).status_code == 303 and _blocks(cid, 0) == []


def test_block_validation(client, parent):
    cid, tok = _setup(client, parent)
    for bad in ({"block_wd_0_start": "13:00", "block_wd_0_end": "08:00"},      # nicht über Mitternacht
                {"block_wd_0_start": "08:00", "block_wd_0_end": ""},           # unvollständig
                {"block_wd_0_start": "08:00", "block_wd_0_end": "09:00", "block_wd_0_name": "x" * 41}):
        r = _save(client, cid, tok, **bad)
        assert r.status_code == 400 and "Sperrzeit" in r.text
    assert _blocks(cid, 0) == []


def test_window_over_midnight(client, parent):
    cid, tok = _setup(client, parent)
    assert _save(client, cid, tok, start_we="16:00", end_we="01:00").status_code == 303
    with SessionLocal() as db:
        rule = next(r for r in db.get(Child, cid).rules if r.weekday == 5)
        assert (rule.window_start, rule.window_end) == (time(16), time(1))
    r = _save(client, cid, tok, start_we="16:00", end_we="07:00")       # später als 06:00 -> Fehler
    assert r.status_code == 400 and "06:00" in r.text
    assert _save(client, cid, tok, start_we="16:00", end_we="16:00").status_code == 400


def test_policy_v2_has_blocks_v1_is_downgraded(client, parent):
    cid, tok = _setup(client, parent)
    _save(client, cid, tok, block_wd_0_start="08:00", block_wd_0_end="13:00", block_wd_0_name="Schule",
          start_we="16:00", end_we="01:00")
    with device(client, tok, "neu", map_to=cid, proto=2) as (ws, _):
        rules = {r.weekday: r for r in _policy(ws).children[0].rules}
        assert rules[0].blocks == [p.Block(start=time(8), end=time(13), name="Schule")]
        assert rules[5].window_end == time(1)
    page = client.get(f"/children/{cid}/zeiten").text
    assert "kennt Sperrzeiten" not in page
    with device(client, tok, "alt", map_to=cid, proto=1) as (ws, _):
        rules = {r.weekday: r for r in _policy(ws).children[0].rules}
        assert rules[0].blocks == [] and rules[0].window_end == time(20)
        assert rules[5].window_start == time(16) and rules[5].window_end == time(23, 59)
    page = client.get(f"/children/{cid}/zeiten").text
    assert "kennt Sperrzeiten" in page and "alt" in page


def test_v1_policy_without_new_features_unchanged(client, parent):
    cid, tok = _setup(client, parent)
    _save(client, cid, tok)
    with device(client, tok, "alt", map_to=cid, proto=1) as (ws, _):
        rules = {r.weekday: r for r in _policy(ws).children[0].rules}
        assert rules[0].window_end == time(20) and rules[0].blocks == []
    assert "kennt Sperrzeiten" not in client.get(f"/children/{cid}/zeiten").text


def test_credit_window_end_over_midnight(client, parent):
    cid, tok = _setup(client, parent)
    _save(client, cid, tok, start_wd="07:00", end_wd="20:00", start_we="07:00", end_we="20:00")
    day = today_local().isoformat()
    ok = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": day, "extra_minutes": "0",
                                                     "window_end": "00:30"}, follow_redirects=False)
    assert ok.status_code == 303
    bad = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": day, "extra_minutes": "0",
                                                      "window_end": "06:30"})
    assert bad.status_code == 400
    early = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": day, "extra_minutes": "0",
                                                        "window_end": "19:00"})
    assert early.status_code == 400


def test_v1_override_over_midnight_capped(client, parent):
    cid, tok = _setup(client, parent)
    _save(client, cid, tok)
    day = today_local()
    client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": day.isoformat(), "extra_minutes": "0",
                                                "window_end": "00:30"})
    with device(client, tok, "alt", map_to=cid, proto=1) as (ws, _):
        exc = _policy(ws).children[0].exceptions
        assert exc[0].window_end_override == time(23, 59)
