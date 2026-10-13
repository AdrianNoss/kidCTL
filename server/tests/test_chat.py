"""Smoke-Tests Chat & Verlängerungsanfragen über WebSocket und WebUI."""

import uuid
from contextlib import contextmanager
from datetime import time, timedelta

from sqlalchemy import select

from kidctl_common import protocol as p

from app.db import SessionLocal
from app.models import Child, DayException, Device, Message
from app.schedule import today_local

from conftest import csrf_from, login


def _cid() -> str:
    return uuid.uuid4().hex


def _recv(ws):
    return p.server_message_adapter.validate_json(ws.receive_text())


@contextmanager
def connected(client, parent):
    """Eltern eingeloggt, Kind Max, Gerät gekoppelt, Linux-User max -> Max, 'gast' nicht zugeordnet."""
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    r = client.post("/devices/pair", data={"name": "PC", "csrf_token": tok})
    code = r.text.split("sudo kidctl enroll http://testserver ")[1][:8]
    enr = client.post(p.ENROLL_PATH, json={"code": code, "hostname": "pc"}).json()
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {enr['token']}"}) as ws:
        ws.send_text(p.Hello(protocol_version=1, agent_version="t", hostname="pc").model_dump_json())
        _recv(ws)
        ws.send_text(p.ReportUsers(users=[p.LocalUser(username="max", uid=1001),
                                          p.LocalUser(username="gast", uid=1002)]).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        _recv(ws)
        with SessionLocal() as db:
            child_id = db.scalar(select(Child.id))
            users = {u.username: u.id for u in db.get(Device, enr["device_id"]).users}
        client.post(f"/devices/{enr['device_id']}/mapping",
                    data={"csrf_token": tok, f"user_{users['max']}": str(child_id)})
        yield ws, child_id, tok


def _request(ws, day, minutes=30, window_end=None, cid=None):
    ws.send_text(p.ExtensionRequest(client_msg_id=cid or _cid(), username="max", day=day,
                                    minutes=minutes, window_end=window_end,
                                    reason="Hausaufgaben fertig").model_dump_json())
    ack, msg = _recv(ws), _recv(ws)
    assert isinstance(ack, p.Ack) and ack.ok and isinstance(msg, p.ChatMessage)
    return msg


def test_child_message_ack_dedupe_and_unmapped(client, parent):
    with connected(client, parent) as (ws, child_id, _):
        cid = _cid()
        ws.send_text(p.ChatSend(client_msg_id=cid, username="max", body="Hallo Mama").model_dump_json())
        ack, msg = _recv(ws), _recv(ws)
        assert ack.ok and isinstance(msg, p.ChatMessage) and msg.id == ack.message_id
        assert msg.from_child and msg.sender_name == "Max" and msg.child_id == child_id

        # Erneut gesendet (Offline-Warteschlange): gleiches Ack, keine zweite Nachricht
        ws.send_text(p.ChatSend(client_msg_id=cid, username="max", body="Hallo Mama").model_dump_json())
        dup = _recv(ws)
        assert dup.ok and dup.message_id == ack.message_id
        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(_recv(ws), p.Pong)

        ws.send_text(p.ChatSend(client_msg_id=_cid(), username="gast", body="x").model_dump_json())
        bad = _recv(ws)
        assert not bad.ok and bad.error == "user_not_mapped"

        page = client.get(f"/children/{child_id}/chat").text
        assert "Hallo Mama" in page
        with SessionLocal() as db:
            assert len(db.scalars(select(Message)).all()) == 1


def test_parent_message_delivered(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        r = client.post(f"/children/{child_id}/chat", data={"csrf_token": tok, "body": "Essen ist fertig"},
                        follow_redirects=False)
        assert r.status_code == 303
        msg = _recv(ws)
        assert isinstance(msg, p.ChatMessage) and not msg.from_child
        assert msg.body == "Essen ist fertig" and msg.sender_name == "mama"
        assert client.post(f"/children/{child_id}/chat", data={"body": "x"}).status_code == 403


def test_request_approve_adjusted(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        today = today_local()
        req = _request(ws, today, minutes=60, window_end=time(21, 0))
        assert req.status == "pending" and req.kind == "extension_request"

        assert "1 Anfrage(n) offen" in client.get("/").text
        page = client.get(f"/children/{child_id}/chat").text
        assert "Genehmigen" in page and "Hausaufgaben fertig" in page

        r = client.post(f"/children/{child_id}/requests/{req.id}", data={
            "csrf_token": tok, "action": "approve", "minutes": "30", "window_end": "20:30"},
            follow_redirects=False)
        assert r.status_code == 303
        upd = _recv(ws)
        assert upd.id == req.id and upd.status == "approved"
        assert upd.approved_minutes == 30 and upd.approved_window_end == time(20, 30)
        with SessionLocal() as db:
            exc = db.scalar(select(DayException))
            assert exc.day == today and exc.extra_minutes == 30 and exc.window_end_override == time(20, 30)

        # Zweite Entscheidung (z. B. anderer Elternteil) wird abgewiesen
        r = client.post(f"/children/{child_id}/requests/{req.id}", data={"csrf_token": tok, "action": "reject"})
        assert r.status_code == 400 and "bereits entschieden" in r.text
        assert "Anfrage(n) offen" not in client.get("/").text


def test_request_reject_and_invalid_approval(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        req = _request(ws, today_local(), minutes=0, window_end=time(21, 0))
        # Fensterende vor dem regulären Ende (20:00) -> abgewiesen, bleibt offen
        r = client.post(f"/children/{child_id}/requests/{req.id}", data={
            "csrf_token": tok, "action": "approve", "minutes": "0", "window_end": "19:00"})
        assert r.status_code == 400 and "nach dem regulären Ende" in r.text
        with SessionLocal() as db:
            assert db.get(Message, req.id).status == "pending"

        client.post(f"/children/{child_id}/requests/{req.id}", data={"csrf_token": tok, "action": "reject"})
        upd = _recv(ws)
        assert upd.status == "rejected" and upd.approved_minutes is None
        with SessionLocal() as db:
            assert db.scalar(select(DayException)) is None


def test_request_past_day_expires_and_sync(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        old = _request(ws, today_local() - timedelta(days=1))
        assert old.status == "expired"
        ws.send_text(p.ExtensionRequest(client_msg_id=_cid(), username="max", day=today_local(),
                                        minutes=0, reason="leer").model_dump_json())
        assert _recv(ws).error == "empty_request"
        client.post(f"/children/{child_id}/chat", data={"csrf_token": tok, "body": "Antwort"})
        _recv(ws)

        ws.send_text(p.ChatSync(username="max", after_id=0).model_dump_json())
        hist = [_recv(ws), _recv(ws)]
        assert [m.id for m in hist] == sorted(m.id for m in hist) and hist[1].body == "Antwort"
        ws.send_text(p.ChatSync(username="max", after_id=hist[0].id).model_dump_json())
        assert _recv(ws).id == hist[1].id
        ws.send_text(p.ChatSync(username="gast").model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        assert isinstance(_recv(ws), p.Pong)  # nicht zugeordnet -> kein Verlauf
