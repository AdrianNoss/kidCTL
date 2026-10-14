"""End-to-End gegen echten Server: Kopplung, Policy-Cache, Zählung, Sync, Widerruf, Offline."""

import asyncio
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pyotp
import pytest

from kidctl_common import protocol as p

from kidctl_agent import cli, store
from kidctl_agent.agent import Agent
from kidctl_agent.logind import SessionInfo

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    env = {**os.environ, "KIDCTL_DEV": "1", "SECRET_KEY": "e2e", "KIDCTL_DATA_DIR": str(tmp_path / "srv"),
           "BASE_URL": f"http://127.0.0.1:{port}", "COOKIE_SECURE": "0",
           "PYTHONPATH": f"{ROOT / 'server'}:{ROOT / 'common'}",
           "KIDCTL_BOOTSTRAP_USER": "mama", "KIDCTL_BOOTSTRAP_PASSWORD": "ein-langes-passwort"}
    for k in ("VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_SUBJECT"):
        env.pop(k, None)
    out = subprocess.run([sys.executable, "-m", "app.cli", "create-parent"], env=env, cwd=ROOT / "server",
                         capture_output=True, text=True, check=True).stdout
    secret = re.search(r"Secret: (\w+)", out).group(1)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                            env=env, cwd=ROOT / "server", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/healthz")
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    web = httpx.Client(base_url=base)
    tok = lambda html: re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)  # noqa: E731
    web.post("/login", data={"csrf_token": tok(web.get("/login").text), "username": "mama",
                             "password": "ein-langes-passwort", "totp": pyotp.TOTP(secret).now()})
    csrf = tok(web.get("/children").text)
    web.post("/children", data={"csrf_token": csrf, "name": "Max"})
    yield base, web, csrf
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:  # uvicorn wartet ggf. auf offene WebSockets
        proc.kill()
        proc.wait(5)


class FakeLogind:
    def __init__(self, sessions):
        self.list = sessions
        self.terminated = []

    async def sessions(self):
        return list(self.list)

    async def terminate(self, sid):
        self.terminated.append(sid)


class FakeNotifier:
    async def notify(self, *a, **kw):
        pass


async def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Bedingung nicht erfüllt")


def test_full_flow(server):
    base, web, csrf = server
    code = re.search(r"enroll \S+ (\w{8})", web.post("/devices/pair", data={"csrf_token": csrf, "name": "PC"}).text).group(1)
    assert cli.enroll(base, code, insecure_http=True) == 0
    device = store.load(store.DEVICE_FILE)
    assert device["server_url"] == base and device["token"]
    with pytest.raises(SystemExit):
        cli.enroll(base, code, insecure_http=True)  # schon gekoppelt

    kind = SessionInfo(id="5", uid=1001, user="kind", session_class="user", active=True,
                       locked=False, state="active")
    logind = FakeLogind([kind])

    async def scenario():
        agent = Agent(logind, FakeNotifier(), device)
        # Testsystem hat keinen User "kind" -> Bericht um ihn ergänzen
        import kidctl_agent.agent as agent_mod
        agent_mod.local_users = lambda: [p.LocalUser(username="kind", uid=1001)]
        run = asyncio.create_task(agent.run())
        await wait_for(lambda: agent.conn.connected)
        # Ohne Zuordnung: niemand eingeschränkt
        await wait_for(lambda: agent.policy is not None)
        assert agent.policy.users == {} and agent.decisions(agent.now_local()) == {}

        page = web.get(f"/devices/{device['device_id']}").text
        user_field = re.search(r'name="(user_\d+)"', page).group(1)
        child_id = re.search(r'<option value="(\d+)"', page).group(1)
        web.post(f"/devices/{device['device_id']}/mapping", data={"csrf_token": csrf, user_field: child_id})
        await wait_for(lambda: agent.policy.users == {"kind": int(child_id)})
        assert store.load(store.POLICY_FILE)["version"] == agent.policy.version  # gecacht
        await wait_for(lambda: ">aktuell<" in web.get(f"/devices/{device['device_id']}").text)

        # Nutzung: 25 min lokal gezählt -> an Server melden -> Übersicht
        today = agent.now_local().date()
        agent.usage.local[("kind", today.isoformat())] = 1500.0
        await agent.send_usage()
        await wait_for(lambda: "0:25 h von" in web.get("/").text)

        # Tick zählt die aktive Sitzung
        await agent.tick_once()
        agent.usage._last_tick -= 5
        await agent.tick_once()
        assert agent.usage.local[("kind", today.isoformat())] >= 1505.0

        # Gerät widerrufen -> Verbindung weg, Policy bleibt für Offline-Durchsetzung
        web.post(f"/devices/{device['device_id']}/revoke", data={"csrf_token": csrf})
        await wait_for(lambda: not agent.conn.connected)
        assert "kind" in agent.decisions(agent.now_local())
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        # Beim Beenden gespeichert
        assert store.load(store.USAGE_FILE)["local"][f"kind|{today.isoformat()}"] >= 1505

    asyncio.run(scenario())


def test_offline_start_uses_cached_policy(server):
    """Agent ohne Server-Erreichbarkeit setzt die gecachte Policy durch."""
    from datetime import time as t
    pol = p.Policy(version="x", timezone="Europe/Berlin", users={"kind": 1}, children=[p.ChildPolicy(
        id=1, name="Max", rules=[p.DayRule(weekday=d, quota_minutes=0, window_start=t(0),
                                          window_end=t(23, 59)) for d in range(7)])])
    store.save(store.POLICY_FILE, pol.model_dump(mode="json"))
    dead = {"server_url": "http://127.0.0.1:1", "device_id": 1, "token": "x", "insecure_http": True}
    agent = Agent(FakeLogind([]), FakeNotifier(), dead)
    d = agent.decisions(agent.now_local())
    assert not d["kind"].allowed
