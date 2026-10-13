import asyncio
from datetime import date

from kidctl_agent.enforcer import GRACE_SECONDS, LOGOUT_TIMEOUT, RECOVER_DELAY, Enforcer
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
        self.logouts = []
        self.dm_restarts = 0

    async def notify(self, user, uid, title, body, critical=False):
        self.sent.append((user, title, body, critical))

    async def logout(self, user, uid):
        self.logouts.append(user)
        return True

    async def restart_display_manager(self):
        self.dm_restarts += 1


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def sess(sid, user="max", cls="user", active=True, locked=False, type="tty", seat=""):
    return SessionInfo(id=sid, uid=1001, user=user, session_class=cls, active=active,
                       locked=locked, state="active", type=type, seat=seat)


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

    from kidctl_agent import usersession as notify

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
        await notify.UserSession().notify(me.pw_name, me.pw_uid, "T", "B")
        await asyncio.sleep(0.5)
        assert started[0].returncode is not None  # beendet
        assert not notify._tasks
    asyncio.run(scenario())


def test_graphical_session_logged_out_via_plasma():
    e, lg, nt, clk = make()
    desk = sess("2", type="wayland", seat="seat0")
    sessions = [desk, sess("3"), sess("c1", user="sddm", cls="greeter", type="wayland", seat="seat0")]
    gone = {"max": Decision(False, 0, QUOTA)}
    run(e.step(DAY, sessions, gone))
    clk.t += GRACE_SECONDS
    run(e.step(DAY, sessions, gone))
    assert nt.logouts == ["max"] and lg.terminated == ["3"]  # TTY direkt, Plasma sauber
    # Plasma hat sich abgemeldet -> nichts weiter
    clk.t += LOGOUT_TIMEOUT
    run(e.step(DAY, [sessions[2]], gone))
    assert lg.terminated == ["3"] and nt.dm_restarts == 0


def test_hard_terminate_and_greeter_recovery_when_logout_fails():
    e, lg, nt, clk = make()
    desk = sess("2", type="wayland", seat="seat0")
    gone = {"max": Decision(False, 0, QUOTA)}
    run(e.step(DAY, [desk], gone))
    clk.t += GRACE_SECONDS
    run(e.step(DAY, [desk], gone))
    clk.t += LOGOUT_TIMEOUT
    run(e.step(DAY, [desk], gone))          # Plasma reagiert nicht -> hart
    assert lg.terminated == ["2"]
    clk.t += RECOVER_DELAY
    run(e.step(DAY, [], gone))              # seat0 leer: kein Greeter, keine Sitzung
    assert nt.dm_restarts == 1
    clk.t += 100
    run(e.step(DAY, [], gone))
    assert nt.dm_restarts == 1               # nur einmal


def test_no_display_manager_restart_if_seat_in_use():
    e, lg, nt, clk = make()
    desk = sess("2", type="wayland", seat="seat0")
    gone = {"max": Decision(False, 0, QUOTA)}
    for dt in (0, GRACE_SECONDS, LOGOUT_TIMEOUT):
        clk.t += dt
        run(e.step(DAY, [desk], gone))
    clk.t += RECOVER_DELAY
    papa = sess("7", user="papa", type="wayland", seat="seat0")  # anderer User am Bildschirm
    run(e.step(DAY, [papa], gone))
    assert nt.dm_restarts == 0
