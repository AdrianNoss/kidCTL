"""Freigabe-Anfragen für gesperrte Programme/Websites: Empfang, Duplikate, Entscheidungen, Policy."""

import uuid
from datetime import timedelta

from sqlalchemy import select

from kidctl_common import protocol as p

from app import access
from app.db import SessionLocal
from app.models import ChildCustomProgram, ChildDomain, ChildProgram, ChildTempGrant, DeviceApp, Message
from app.schedule import today_local

from conftest import recv
from test_chat import connected


def _ask(ws, kind, key, name, reason="", cid=None):
    ws.send_text(p.AccessRequest(client_msg_id=cid or uuid.uuid4().hex, username="max", access_kind=kind, key=key,
                                 name=name, reason=reason).model_dump_json())
    return recv(ws)


def _policy(ws, child_id) -> p.ChildPolicy:
    """Zuletzt gesendete Policy (jede Entscheidung schickt eine; Ping markiert das Ende)."""
    ws.send_text(p.Ping().model_dump_json())
    last = None
    while not isinstance(msg := recv(ws, keep_policy=True), p.Pong):
        if isinstance(msg, p.Policy):
            last = msg
    return next(c for c in last.children if c.id == child_id)


def _decide(client, tok, child_id, mid, action):
    return client.post(f"/children/{child_id}/access/{mid}", data={"csrf_token": tok, "action": action},
                       follow_redirects=False)


def test_valid_key():
    assert access.valid_key("domain", "domain:WWW.Example.COM") == "domain:www.example.com"
    assert access.valid_key("domain", "domain:nicht gültig") is None
    assert access.valid_key("domain", "exe:/usr/bin/x") is None
    assert access.valid_key("program", "exe:/usr/bin/git") == "exe:/usr/bin/git"
    assert access.valid_key("program", "exe:/usr/bin/../bin/sh") is None
    assert access.valid_key("program", "flatpak:org.tuxpaint.Tuxpaint") == "flatpak:org.tuxpaint.Tuxpaint"
    assert access.valid_key("program", "flatpak:x y") is None and access.valid_key("program", "domain:a.de") is None


def test_request_dedupe_and_reject(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        ack = _ask(ws, "program", "exe:/usr/bin/socat1", "socat1", "für Informatik")
        msg = recv(ws)
        assert ack.ok and msg.kind == "access_request" and msg.status == "pending"
        assert (msg.access_kind, msg.access_key, msg.access_name, msg.body) == (
            "program", "exe:/usr/bin/socat1", "socat1", "für Informatik")
        again = _ask(ws, "program", "exe:/usr/bin/socat1", "socat1")          # offene Anfrage: keine zweite
        assert again.ok and again.message_id == ack.message_id
        bad = _ask(ws, "domain", "domain:-kaputt-", "x")
        assert not bad.ok and bad.error == "invalid_key"
        page = client.get(f"/children/{child_id}/chat").text
        assert "Freigabe-Anfrage" in page and "Dauerhaft erlauben" in page and "für Informatik" in page
        assert "Kinder" in client.get("/").text

        assert _decide(client, tok, child_id, ack.message_id, "reject").status_code == 303
        upd = recv(ws)
        assert upd.id == ack.message_id and upd.status == "rejected" and upd.access_scope is None
        assert _decide(client, tok, child_id, ack.message_id, "today").status_code == 400  # schon entschieden
        assert _decide(client, tok, child_id, ack.message_id, "kaputt").status_code == 404
        with SessionLocal() as db:
            assert db.scalars(select(ChildProgram)).all() == [] and db.scalars(select(ChildTempGrant)).all() == []
        # nach der Entscheidung ist eine neue Anfrage möglich
        assert _ask(ws, "program", "exe:/usr/bin/socat1", "socat1").message_id != ack.message_id


def test_permanent_program_and_domain(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        with SessionLocal() as db:
            db.add(DeviceApp(device_id=1, key="exe:/usr/bin/krita", name="Krita"))
            db.add(ChildDomain(child_id=child_id, domain="spiele.de", action="deny"))
            db.commit()
        krita = _ask(ws, "program", "exe:/usr/bin/krita", "Krita")
        recv(ws)
        git = _ask(ws, "program", "exe:/usr/bin/git", "git")
        recv(ws)
        site = _ask(ws, "domain", "domain:spiele.de", "spiele.de")
        recv(ws)
        for ack in (krita, git, site):
            assert _decide(client, tok, child_id, ack.message_id, "permanent").status_code == 303
        pol = _policy(ws, child_id)
        with SessionLocal() as db:
            assert set(db.scalars(select(ChildProgram.key))) == {"exe:/usr/bin/krita", "exe:/usr/bin/git"}
            assert [(c.key, c.name) for c in db.scalars(select(ChildCustomProgram))] == [("exe:/usr/bin/git", "git")]
            assert [(d.domain, d.action) for d in db.scalars(select(ChildDomain))] == [("spiele.de", "allow")]
            assert db.get(Message, git.message_id).access_scope == "permanent"
        assert {"exe:/usr/bin/krita", "exe:/usr/bin/git"} <= set(pol.allowed_programs)


def test_today_grant_in_policy(client, parent):
    with connected(client, parent) as (ws, child_id, tok):
        client.post(f"/children/{child_id}/web", data={"csrf_token": tok, "web_mode": "whitelist"})
        prog = _ask(ws, "program", "flatpak:org.tuxpaint.Tuxpaint", "Tux Paint", "Kunst")
        recv(ws)
        site = _ask(ws, "domain", "domain:wikipedia.org", "wikipedia.org")
        recv(ws)
        for ack in (prog, site):
            assert _decide(client, tok, child_id, ack.message_id, "today").status_code == 303
        pol = _policy(ws, child_id)
        today = today_local()
        assert {(g.key, g.day) for g in pol.temp_grants} >= {("flatpak:org.tuxpaint.Tuxpaint", today),
                                                             ("domain:wikipedia.org", today)}
        assert "flatpak:org.tuxpaint.Tuxpaint" in pol.programs_on(today)
        assert "flatpak:org.tuxpaint.Tuxpaint" not in pol.programs_on(today + timedelta(days=1))
        assert "flatpak:org.tuxpaint.Tuxpaint" not in pol.allowed_programs
        with SessionLocal() as db:
            assert db.scalars(select(ChildProgram)).all() == [] and db.scalars(select(ChildDomain)).all() == []
        page = client.get(f"/children/{child_id}/chat").text
        assert "Heute erlaubt" in page


def test_web_on():
    child = p.ChildPolicy(id=1, name="M", rules=[], web=p.WebFilter(mode="whitelist", allow=["a.de"]),
                          temp_grants=[p.TempGrant(key="domain:b.de", day=today_local()),
                                       p.TempGrant(key="domain:c.de", day=today_local() - timedelta(days=1)),
                                       p.TempGrant(key="exe:/usr/bin/x", day=today_local())])
    assert child.web_on(today_local()).allow == ["a.de", "b.de"]
    assert child.web_on(today_local() + timedelta(days=1)).allow == ["a.de"]
    off = child.model_copy(update={"web": p.WebFilter(mode="off")})
    assert off.web_on(today_local()).mode == "off" and off.web_on(today_local()).allow == []
    assert child.programs_on(today_local()) == ["exe:/usr/bin/x"]
