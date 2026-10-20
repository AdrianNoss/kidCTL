"""Agent-Takt: Start-Synchronisation gegen Fehlalarme mit veralteter gecachter Policy."""

import asyncio
from datetime import time

from kidctl_common import protocol as p

from kidctl_agent import agent as agent_mod
from kidctl_agent import store
from kidctl_agent.agent import Agent
from kidctl_agent.logind import SessionInfo


class FakeLogind:
    async def sessions(self):
        return [SessionInfo(id="1", uid=1001, user="kind", session_class="user", active=True,
                            locked=False, state="active")]

    async def terminate(self, sid):
        pass


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def notify(self, user, uid, title, body, critical=False, on_action=None, action=None):
        self.sent.append(title)

    async def logout(self, user, uid):
        return True

    async def restart_display_manager(self):
        pass


class FakeConn:
    connected = True

    async def send(self, msg):
        return True


def policy(quota, version):
    return p.Policy(version=version, timezone="Europe/Berlin", users={"kind": 1}, children=[
        p.ChildPolicy(id=1, name="Max", rules=[p.DayRule(weekday=d, quota_minutes=quota,
                                                        window_start=time(0), window_end=time(23, 59, 59))
                                              for d in range(7)])])


def make_agent(monkeypatch, clock):
    store.save(store.POLICY_FILE, policy(0, "alt").model_dump(mode="json"))  # Cache: kein Kontingent
    monkeypatch.setattr(agent_mod, "boottime", lambda: clock[0])
    nt = FakeNotifier()
    a = Agent(FakeLogind(), nt, {"server_url": "https://x", "device_id": 1, "token": "t"})
    a.conn = FakeConn()
    return a, nt


def test_no_false_alarm_before_server_policy(monkeypatch):
    clock = [100.0]
    a, nt = make_agent(monkeypatch, clock)

    async def scenario():
        await a.tick_once()                      # gecachte Policy sagt "gesperrt" – noch nicht durchsetzen
        assert nt.sent == []
        await a.on_message(policy(60, "neu"))    # Server liefert aktuelle Policy
        await a.tick_once()
        assert "Bildschirmzeit zu Ende" not in nt.sent
    asyncio.run(scenario())


def test_cached_policy_enforced_when_server_unreachable(monkeypatch):
    clock = [100.0]
    a, nt = make_agent(monkeypatch, clock)

    async def scenario():
        await a.tick_once()
        assert nt.sent == []
        clock[0] += agent_mod.STARTUP_SYNC
        await a.tick_once()                      # kein Server -> nach Wartezeit gecachte Policy
        assert nt.sent == ["Bildschirmzeit zu Ende"]
    asyncio.run(scenario())


def test_sdnotify_ready(tmp_path, monkeypatch):
    import socket as sk

    from kidctl_agent import sdnotify
    path = str(tmp_path / "notify.sock")
    srv = sk.socket(sk.AF_UNIX, sk.SOCK_DGRAM)
    srv.bind(path)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    assert sdnotify.notify("READY=1")
    assert srv.recv(64) == b"READY=1"
    srv.close()
    monkeypatch.delenv("NOTIFY_SOCKET")
    assert not sdnotify.notify("READY=1")
