import asyncio
from datetime import date

from kidctl_agent.enforcer import GRACE_SECONDS, Enforcer
from kidctl_agent.logind import SessionInfo
from kidctl_agent.rules import QUOTA, Decision

DAY = date(2026, 10, 13)


class FakeLogind:
    def __init__(self):
        self.terminated = []

    async def terminate(self, sid):
        self.terminated.append(sid)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def notify(self, user, uid, title, body, critical=False):
        self.sent.append((user, title, body, critical))


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def sess(sid, user="max", cls="user", active=True, locked=False):
    return SessionInfo(id=sid, uid=1001, user=user, session_class=cls, active=active,
                       locked=locked, state="active")


def make():
    lg, nt, clk = FakeLogind(), FakeNotifier(), Clock()
    return Enforcer(lg, nt, clk), lg, nt, clk


def run(coro):
    return asyncio.run(coro)


def test_warnings_once_per_threshold():
    e, _, nt, _ = make()
    s = [sess("1")]
    for remaining in (1200, 900, 800, 300, 290, 60, 30):
        run(e.step(DAY, s, {"max": Decision(True, remaining)}))
    titles = [b for _, _, b, _ in nt.sent]
    assert titles == ["Noch 15 Minuten – danach wirst du abgemeldet.",
                      "Noch 5 Minuten – danach wirst du abgemeldet.",
                      "Noch 1 Minute – danach wirst du abgemeldet."]
    assert nt.sent[-1][3] is True  # letzte Warnung kritisch


def test_login_with_little_time_warns_once():
    e, _, nt, _ = make()
    run(e.step(DAY, [sess("1")], {"max": Decision(True, 120)}))
    assert len(nt.sent) == 1


def test_countdown_then_terminate_all_user_sessions():
    e, lg, nt, clk = make()
    sessions = [sess("1"), sess("2"), sess("c1", user="sddm", cls="greeter"), sess("9", user="papa")]
    gone = {"max": Decision(False, 0, QUOTA)}
    run(e.step(DAY, sessions, gone))
    assert nt.sent[0][1] == "Bildschirmzeit zu Ende" and nt.sent[0][3]
    clk.t += GRACE_SECONDS - 1
    run(e.step(DAY, sessions, gone))
    assert lg.terminated == []
    clk.t += 1
    run(e.step(DAY, sessions, gone))
    assert lg.terminated == ["1", "2"]  # Greeter und nicht zugeordneter User unangetastet


def test_countdown_cancelled_when_time_granted():
    e, lg, nt, clk = make()
    s = [sess("1")]
    run(e.step(DAY, s, {"max": Decision(False, 0, QUOTA)}))
    clk.t += 30
    run(e.step(DAY, s, {"max": Decision(True, 1800)}))
    clk.t += 60
    run(e.step(DAY, s, {"max": Decision(True, 1770)}))
    assert lg.terminated == [] and nt.sent[1][1] == "Bildschirmzeit verlängert"


def test_unmapped_users_never_touched():
    e, lg, nt, clk = make()
    run(e.step(DAY, [sess("1", user="papa")], {}))
    clk.t += 1000
    run(e.step(DAY, [sess("1", user="papa")], {}))
    assert lg.terminated == [] and nt.sent == []


def test_notifier_kills_hanging_notify_send(monkeypatch, tmp_path):
    """notify-send ohne Benachrichtigungsdienst hängt -> wird nach Timeout beendet, kein Fehler."""
    import os
    import pwd

    from kidctl_agent import notify

    me = pwd.getpwuid(os.getuid())
    started = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd, **kw):
        assert cmd[0] == "setpriv" and "runuser" not in cmd
        proc = await real_exec("sleep", "60")
        started.append(proc)
        return proc

    monkeypatch.setattr(notify.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(notify, "TIMEOUT", 0.2)
    monkeypatch.setattr(notify.os.path, "exists", lambda p: True)

    async def scenario():
        await notify.Notifier().notify(me.pw_name, me.pw_uid, "T", "B")
        await asyncio.sleep(0.5)
        assert started[0].returncode is not None  # beendet
        assert not notify._tasks
    asyncio.run(scenario())
