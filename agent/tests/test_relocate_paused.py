"""Etappe 3 Multi-Tenant (Agent): Server-Umzug mit Prüfung, Ruhemodus."""

import asyncio

from kidctl_common import protocol as p

from kidctl_agent import store
from kidctl_agent.connection import Connection
from test_agent import make_agent


class FakeWS:
    closed = False

    async def close(self):
        self.closed = True


def _conn(monkeypatch, accepted: bool):
    device = {"server_url": "https://alt.example.org", "device_id": 7, "token": "geheim"}
    store.save(store.DEVICE_FILE, device)
    conn = Connection(dict(device), handler=None)
    probed = []

    async def probe(url):
        probed.append(url)
        return accepted
    monkeypatch.setattr(conn, "_probe", probe)
    conn._ws = FakeWS()
    return conn, probed


def test_relocate_only_when_new_server_accepts_token(monkeypatch):
    conn, probed = _conn(monkeypatch, accepted=True)
    ws = conn._ws
    asyncio.run(conn._relocate("https://neu.example.org"))
    assert probed == ["wss://neu.example.org" + p.WS_PATH]
    saved = store.load(store.DEVICE_FILE)
    assert saved["server_url"] == "https://neu.example.org" and saved["previous_server_url"] == "https://alt.example.org"
    assert saved["token"] == "geheim" and conn.url == "wss://neu.example.org" + p.WS_PATH and ws.closed


def test_relocate_rejected_keeps_old_server(monkeypatch):
    conn, _ = _conn(monkeypatch, accepted=False)
    asyncio.run(conn._relocate("https://falsch.example.org"))
    assert store.load(store.DEVICE_FILE)["server_url"] == "https://alt.example.org"
    assert conn.url == "wss://alt.example.org" + p.WS_PATH and not conn._ws.closed


def test_relocate_to_unencrypted_address_refused(monkeypatch):
    conn, probed = _conn(monkeypatch, accepted=True)
    for url in ("http://neu.example.org", "ftp://neu.example.org", "neu.example.org"):
        asyncio.run(conn._relocate(url))
    assert probed == []          # Token geht nicht an das Ziel
    assert store.load(store.DEVICE_FILE)["server_url"] == "https://alt.example.org" and not conn._ws.closed


def test_relocate_to_same_server_is_noop(monkeypatch):
    conn, probed = _conn(monkeypatch, accepted=True)
    asyncio.run(conn._relocate("https://alt.example.org"))
    assert probed == []


def test_paused_policy_lifts_restrictions_and_shows_paused(monkeypatch):
    clock = [100.0]
    a, nt = make_agent(monkeypatch, clock)
    paused = p.Policy(version="p", timezone="Europe/Berlin", users={}, children=[], paused=True,
                      paused_users=["kind"])

    async def scenario():
        await a.on_message(paused)
        await a.tick_once()
    asyncio.run(scenario())
    st = a.tray_state("kind")
    assert not st.restricted and st.paused and a.decisions(a.now_local()) == {}
    assert not a.tray_state("papa").paused                       # nie zugeordneter User: kein Hinweis
    assert nt.sent == []                                         # keine Warnung, keine Abmeldung


def test_old_agent_ignores_unknown_fields_and_messages():
    # Ältere Agenten: unbekannte Nachricht -> ValidationError (wird übersprungen); extra Felder -> ignoriert
    import pytest
    from pydantic import ValidationError
    raw = p.Relocate(server_url="https://neu.example.org").model_dump_json().replace("relocate", "relocate_v9")
    with pytest.raises(ValidationError):
        p.server_message_adapter.validate_json(raw)
    old = p.Policy.model_validate({"type": "policy", "version": "v", "timezone": "Europe/Berlin", "users": {},
                                   "children": [], "paused": True, "paused_users": ["kind"], "neu": 1})
    assert old.users == {}
