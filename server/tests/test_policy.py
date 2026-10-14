"""Smoke-Tests Policy-Verteilung, geräteübergreifende Nutzungszeit, Offline-Alarm."""

from contextlib import ExitStack, contextmanager
from datetime import time, timedelta

import pytest
from sqlalchemy import select

from kidctl_common import protocol as p

from app import alarms
from app.db import SessionLocal
from app.models import Child, Device, utcnow
from app.schedule import today_local

from conftest import csrf_from, login, recv


def _policy(ws) -> p.Policy:
    while True:
        m = recv(ws, keep_policy=True)
        if isinstance(m, p.Policy):
            return m


def _usage(ws) -> p.UsageUpdate:
    while True:
        m = recv(ws, keep_policy=True)
        if isinstance(m, p.UsageUpdate):
            return m


def _setup(client, parent) -> tuple[int, str]:
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    with SessionLocal() as db:
        return db.scalar(select(Child.id)), tok


@contextmanager
def device(client, tok, name, users=("max",), map_to=None):
    """Koppelt + verbindet ein Gerät, meldet User, ordnet sie optional einem Kind zu."""
    r = client.post("/devices/pair", data={"name": name, "csrf_token": tok})
    code = r.text.split("sudo kidctl enroll http://testserver ")[1][:8]
    enr = client.post(p.ENROLL_PATH, json={"code": code, "hostname": name}).json()
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {enr['token']}"}) as ws:
        ws.send_text(p.Hello(protocol_version=1, agent_version="t", hostname=name).model_dump_json())
        assert isinstance(recv(ws), p.Welcome)
        first = _policy(ws)
        assert first.users == {}  # noch nichts zugeordnet -> niemand eingeschränkt
        ws.send_text(p.ReportUsers(users=[p.LocalUser(username=u, uid=1001 + i)
                                          for i, u in enumerate(users)]).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(recv(ws), p.Pong)
        if map_to is not None:
            with SessionLocal() as db:
                ids = {u.username: u.id for u in db.get(Device, enr["device_id"]).users}
            client.post(f"/devices/{enr['device_id']}/mapping",
                        data={"csrf_token": tok, f"user_{ids[users[0]]}": str(map_to)})
        yield ws, enr["device_id"]


def test_policy_on_connect_mapping_and_ack(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", users=("max", "papa"), map_to=cid) as (ws, dev_id):
        pol = _policy(ws)
        assert pol.users == {"max": cid}  # papa nicht zugeordnet -> nicht enthalten
        assert pol.timezone == "Europe/Berlin" and len(pol.children) == 1
        assert [r.weekday for r in pol.children[0].rules] == list(range(7))
        assert _usage(ws).others_seconds == 0

        assert "noch nicht übertragen" in client.get(f"/devices/{dev_id}").text
        ws.send_text(p.PolicyAck(version=pol.version).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        recv(ws)
        assert ">aktuell<" in client.get(f"/devices/{dev_id}").text

        # Regeländerung -> neue Policy wird sofort gepusht
        client.post(f"/children/{cid}/schedule", data={
            "csrf_token": tok, "quota_wd": "45", "start_wd": "15:00", "end_wd": "19:00",
            "quota_we": "120", "start_we": "10:00", "end_we": "20:00"})
        pol2 = _policy(ws)
        assert pol2.version != pol.version
        assert pol2.children[0].rules[0].quota_minutes == 45
        assert pol2.children[0].rules[0].window_start == time(15)
        assert "veraltet" in client.get(f"/devices/{dev_id}").text


def test_credit_and_exception_delete_push_policy(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        _policy(ws)
        today = today_local()
        client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": today.isoformat(),
                                                    "extra_minutes": "30", "window_end": "21:00"})
        exc = _policy(ws).children[0].exceptions
        assert len(exc) == 1 and exc[0].day == today and exc[0].extra_minutes == 30
        assert exc[0].window_end_override == time(21)

        page = client.get(f"/children/{cid}").text
        exc_id = page.split(f"/children/{cid}/exceptions/")[1].split("/")[0]
        client.post(f"/children/{cid}/exceptions/{exc_id}/delete", data={"csrf_token": tok})
        assert _policy(ws).children[0].exceptions == []


def test_child_delete_removes_restriction(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, _):
        assert _policy(ws).users == {"max": cid}
        client.post(f"/children/{cid}/delete", data={"csrf_token": tok})
        pol = _policy(ws)
        assert pol.users == {} and pol.children == []


def test_usage_summed_across_devices(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local()
    with ExitStack() as stack:
        ws_a, a = stack.enter_context(device(client, tok, "laptop", map_to=cid))
        ws_b, b = stack.enter_context(device(client, tok, "desktop", map_to=cid))
        for ws in (ws_a, ws_b):
            _policy(ws), _usage(ws)  # Startzustand nach der Zuordnung

        ws_a.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=1500)])
                       .model_dump_json())
        upd = _usage(ws_b)
        assert upd.child_id == cid and upd.day == today and upd.others_seconds == 1500

        # Kleinerer (veralteter) Zählerstand ändert nichts; unbekannte User/falsche Tage ignoriert
        ws_a.send_text(p.UsageReport(entries=[
            p.UsageEntry(username="max", day=today, seconds=600),
            p.UsageEntry(username="gast", day=today, seconds=999),
            p.UsageEntry(username="max", day=today - timedelta(days=30), seconds=999),
        ]).model_dump_json())
        ws_b.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=3000)])
                       .model_dump_json())
        assert _usage(ws_a).others_seconds == 3000
        ws_b.send_text(p.Ping().model_dump_json())
        assert isinstance(recv(ws_b), p.Pong)  # A hat B nichts Neues gemeldet

        # 25 + 50 = 75 min bei 60 min Kontingent -> Überziehung angezeigt
        dash = client.get("/").text
        assert "1:15 h <span class=\"muted\">von 1:00 h" in dash and "überzogen um 15 min" in dash


def test_reconnect_gets_usage_of_other_devices(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local()
    with device(client, tok, "laptop", map_to=cid) as (ws_a, _):
        _policy(ws_a)
        ws_a.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=today, seconds=1200)])
                       .model_dump_json())
        ws_a.send_text(p.Ping().model_dump_json())
        recv(ws_a)
    with device(client, tok, "desktop", map_to=cid) as (ws_b, _):
        _policy(ws_b)
        assert _usage(ws_b).others_seconds == 1200


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(alarms.push, "send_to_parents_sync",
                        lambda title, body, url="/", tag=None, parent_id=None: sent.append((title, url)))
    return sent


def test_offline_alarm_once_per_phase(client, parent, pushes):
    _, tok = _setup(client, parent)
    with device(client, tok, "laptop"):
        pass
    with SessionLocal() as db:
        dev = db.scalar(select(Device))
        dev.last_seen_at = utcnow() - timedelta(hours=47)
        db.commit()
        dev_id = dev.id
    assert alarms.check_once() == 0

    with SessionLocal() as db:
        db.get(Device, dev_id).last_seen_at = utcnow() - timedelta(hours=49)
        db.commit()
    assert alarms.check_once() == 1
    assert pushes == [("Gerät „laptop“ offline", f"/devices/{dev_id}")]
    assert alarms.check_once() == 0  # nicht erneut in derselben Offline-Phase

    # Gerät hat sich nach dem Alarm wieder gemeldet und ist danach erneut zu lange weg -> neuer Alarm
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        dev.offline_alerted_at = utcnow() - timedelta(hours=10)
        dev.last_seen_at = utcnow() - timedelta(hours=2)
        dev.alert_after_hours = 3
        db.commit()
    assert alarms.check_once() == 0
    with SessionLocal() as db:
        db.get(Device, dev_id).alert_after_hours = 1
        db.commit()
    assert alarms.check_once() == 1


def test_no_offline_alarm_for_online_or_revoked(client, parent, pushes):
    _, tok = _setup(client, parent)
    with device(client, tok, "laptop") as (_, dev_id):
        with SessionLocal() as db:
            db.get(Device, dev_id).last_seen_at = utcnow() - timedelta(hours=100)
            db.commit()
        assert alarms.check_once() == 0  # verbunden
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        dev.last_seen_at = utcnow() - timedelta(hours=100)
        dev.revoked_at = utcnow()
        db.commit()
    assert alarms.check_once() == 0 and pushes == []
