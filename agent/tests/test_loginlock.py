import asyncio
import os
import stat
from datetime import time

from kidctl_common import protocol as p

from kidctl_agent import agent as agent_mod
from kidctl_agent import loginlock, store
from kidctl_agent.agent import Agent
from test_agent import FakeLogind, FakeNotifier


def read(name):
    return (loginlock.RUN_DIR / name).read_text()


def test_lock_file_written_and_permissions():
    lock = loginlock.LoginLock()
    lock.update({"max", "lena"})
    assert read(loginlock.LOCKED_FILE) == "lena\nmax\n"
    assert "Bildschirmzeit" in read(loginlock.MESSAGE_FILE)
    mode = stat.S_IMODE(os.stat(loginlock.RUN_DIR / loginlock.LOCKED_FILE).st_mode)
    assert mode == 0o644  # pam_listfile lehnt world-writable Dateien ab
    lock.update(set())
    assert read(loginlock.LOCKED_FILE) == ""


def test_is_admin_real_lookup():
    """Echte Prüfung (die Fixture ersetzt is_admin nur im Modul-Namensraum)."""
    import importlib
    real = importlib.reload(loginlock).is_admin
    importlib.reload(loginlock)
    assert real("root")
    assert not real("gibt-es-nicht-xyz")


def policy(users):
    return p.Policy(version="v", timezone="Europe/Berlin", users=users, children=[
        p.ChildPolicy(id=1, name="Max", rules=[p.DayRule(weekday=d, quota_minutes=0, window_start=time(0),
                                                        window_end=time(23, 59, 59)) for d in range(7)])])


def test_agent_writes_lock_immediately_and_spares_admins(monkeypatch, tmp_path):
    store.save(store.POLICY_FILE, policy({"kind": 1, "papa": 1}).model_dump(mode="json"))
    monkeypatch.setattr(loginlock, "RUN_DIR", tmp_path / "run2")
    monkeypatch.setattr(agent_mod, "is_admin", lambda u: u == "papa")
    a = Agent(FakeLogind(), FakeNotifier(), {"server_url": "http://x", "device_id": 1, "token": "t"})
    asyncio.run(a.tick_once())  # noch innerhalb der Start-Wartezeit
    assert (tmp_path / "run2" / loginlock.LOCKED_FILE).read_text() == "kind\n"  # papa (Admin) nie
    assert "papa" not in a.decisions(a.now_local())
