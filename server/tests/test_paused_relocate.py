"""Etappe 3 Multi-Tenant (Server-Seite): Ruhemodus in der Policy, Server-Umzug."""

import dataclasses

from kidctl_common import protocol as p

from app.models import Family, utcnow
from app.routes import api
from conftest import SessionLocal, recv
from test_policy import _policy, _setup, device


def test_paused_family_gets_empty_policy_with_paused_users(client, parent):
    import asyncio

    from app import policy
    cid, tok = _setup(client, parent)
    with device(client, tok, "PC", users=("max", "papa"), map_to=cid) as (ws, dev_id):
        active = _policy(ws)
        assert active.users == {"max": cid} and not active.paused
        with SessionLocal() as db:
            db.get(Family, 1).paused_at = utcnow()
            db.commit()
            client.portal.call(policy.send_policy, db, dev_id)   # wie nach Ablauf des Abos
        pol = _policy(ws)
        # auch ältere Agenten: keine Zuordnung -> keine Einschränkung; neuere zeigen „pausiert“
        assert pol.paused and pol.users == {} and pol.children == [] and pol.paused_users == ["max"]
        assert pol.version != active.version
        with SessionLocal() as db:
            db.get(Family, 1).paused_at = None             # wieder bezahlt -> Regeln sofort zurück
            db.commit()
            client.portal.call(policy.send_policy, db, dev_id)
        assert _policy(ws).users == {"max": cid}


def test_relocate_sent_after_policy_when_configured(client, parent, monkeypatch):
    cid, tok = _setup(client, parent)
    monkeypatch.setattr(api, "settings", dataclasses.replace(api.settings, relocate_to="https://neu.example.org"))
    r = client.post("/devices/pair", data={"name": "PC", "csrf_token": tok})
    code = r.text.split("sudo kidctl enroll http://testserver ")[1][:8]
    enr = client.post(p.ENROLL_PATH, json={"code": code, "hostname": "pc"}).json()
    with client.websocket_connect(p.WS_PATH, headers={"Authorization": f"Bearer {enr['token']}"}) as ws:
        ws.send_text(p.Hello(protocol_version=1, agent_version="t", hostname="pc").model_dump_json())
        assert isinstance(recv(ws), p.Welcome)
        msg = recv(ws)                                     # Policy/Nutzung übersprungen
        assert isinstance(msg, p.Relocate) and msg.server_url == "https://neu.example.org"


def test_relocate_url_must_be_https():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        p.Relocate(server_url="http://unsicher.example.org")
    with pytest.raises(ValidationError):
        p.Relocate(server_url="https://evil.example.org\\nX")
    assert p.Relocate(server_url="https://neu.example.org:8443/kidctl").server_url
