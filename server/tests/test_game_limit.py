"""Spiele-Limit: Wochenvorlage, Gutschrift, Spielzeit-Anfrage, Policy, geräteübergreifende Spielzeit."""

import uuid
from datetime import time

from sqlalchemy import select

from kidctl_common import protocol as p

from app.db import SessionLocal
from app.models import Child, DayException, UsageRecord
from app.schedule import today_local

from conftest import recv
from test_chat import connected
from test_schedule import _setup


def _games(child_id):
    with SessionLocal() as db:
        return {r.weekday: r.game_minutes for r in db.get(Child, child_id).rules}


def test_schedule_game_minutes(client, parent):
    cid, tok = _setup(client, parent)
    base = {"csrf_token": tok, "quota_wd": "90", "start_wd": "14:00", "end_wd": "19:30",
            "quota_we": "180", "start_we": "09:00", "end_we": "21:00"}
    assert client.post(f"/children/{cid}/schedule", data=base | {"games_wd": "45", "games_we": ""},
                       follow_redirects=False).status_code == 303
    g = _games(cid)
    assert all(g[d] == 45 for d in range(5)) and g[5] is None and g[6] is None
    page = client.get(f"/children/{cid}").text
    assert "davon Spiele" in page and 'name="games_wd" min="0" max="1440" step="5" value="45"' in page
    r = client.post(f"/children/{cid}/schedule", data=base | {"games_wd": "abc"})
    assert r.status_code == 400 and "Werktage (Mo–Fr) (Spiele)" in r.text


def test_game_credit(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local()
    r = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": today.isoformat(), "extra_games": "15"})
    assert r.status_code == 400 and "kein Spiele-Limit" in r.text           # ohne Limit unnötig
    with SessionLocal() as db:
        for rule in db.get(Child, cid).rules:
            rule.game_minutes = 60
        db.commit()
    r = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": today.isoformat(), "extra_games": "15"},
                    follow_redirects=False)
    assert r.status_code == 303
    client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": today.isoformat(), "extra_games": "10"})
    with SessionLocal() as db:
        exc = db.scalar(select(DayException))
        assert (exc.extra_minutes, exc.extra_game_minutes) == (0, 25)
    assert "+0:25 h" in client.get(f"/children/{cid}").text


def test_game_request_and_policy(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        with SessionLocal() as db:
            for rule in db.get(Child, child_id).rules:
                rule.game_minutes = 60
            db.commit()
        today = today_local()
        bad = p.ExtensionRequest(client_msg_id=uuid.uuid4().hex, username="max", day=today, minutes=0,
                                 window_end=time(21), reason="x", target="games")
        ws.send_text(bad.model_dump_json())
        assert recv(ws).error == "invalid_game_request"
        ws.send_text(p.ExtensionRequest(client_msg_id=uuid.uuid4().hex, username="max", day=today, minutes=20,
                                        reason="Level fertig", target="games").model_dump_json())
        ack, msg = recv(ws), recv(ws)
        assert ack.ok and msg.request_target == "games" and msg.requested_minutes == 20
        page = client.get(f"/children/{child_id}/chat").text
        assert "Spielzeit-Anfrage" in page
        r = client.post(f"/children/{child_id}/requests/{msg.id}",
                        data={"csrf_token": tok, "action": "approve", "minutes": "15", "window_end": "22:00"},
                        follow_redirects=False)
        assert r.status_code == 303
        upd = recv(ws)
        assert upd.status == "approved" and upd.approved_minutes == 15 and upd.approved_window_end is None
        with SessionLocal() as db:
            exc = db.scalar(select(DayException))
            assert (exc.extra_minutes, exc.window_end_override, exc.extra_game_minutes) == (0, None, 15)
        ws.send_text(p.Ping().model_dump_json())
        pol = None
        while not isinstance(m := recv(ws, keep_policy=True), p.Pong):
            if isinstance(m, p.Policy):
                pol = m
        child = pol.children[0]
        assert {r.game_minutes for r in child.rules} == {60}
        assert [(e.day, e.extra_game_minutes) for e in child.exceptions] == [(today, 15)]

        # Spielzeit wird geräteübergreifend gespeichert (Maximum-Regel wie die Bildschirmzeit)
        ws.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=600,
                                                         game_seconds=300)]).model_dump_json())
        ws.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=600,
                                                         game_seconds=200)]).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        while not isinstance(recv(ws, keep_policy=True), p.Pong):
            pass
        with SessionLocal() as db:
            rec = db.scalar(select(UsageRecord))
            assert (rec.seconds, rec.game_seconds) == (600, 300)
