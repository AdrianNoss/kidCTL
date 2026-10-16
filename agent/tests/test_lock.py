"""„Jetzt sperren“ im Agenten: Regel, Ablauf (auch offline), Durchsetzung mit Vorwarnung."""

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from kidctl_common.protocol import ChildPolicy, DayRule

from kidctl_agent.enforcer import Enforcer
from kidctl_agent.rules import LOCKED, Decision, evaluate
from test_enforcer import DAY, Clock, FakeLogind, FakeNotifier, sess

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def child(**kw):
    return ChildPolicy(id=1, name="Max", rules=[DayRule(weekday=d, quota_minutes=120, window_start=time(0),
                                                        window_end=time(23, 59)) for d in range(7)], **kw)


def test_unlocked_child_allowed():
    assert evaluate(child(), NOW, 0).allowed


def test_locked_until_release():
    d = evaluate(child(locked=True), NOW, 0)
    assert not d.allowed and d.reason == LOCKED and d.remaining_seconds == 0


def test_timed_lock_runs_out_without_server():
    until = NOW.astimezone(timezone.utc) + timedelta(minutes=30)
    c = child(locked=True, locked_until=until)
    assert evaluate(c, NOW, 0).reason == LOCKED
    assert evaluate(c, NOW + timedelta(minutes=29), 0).reason == LOCKED
    assert evaluate(c, NOW + timedelta(minutes=30), 0).allowed      # abgelaufen -> wieder Regeln


def test_lock_beats_remaining_quota_and_naive_now():
    c = child(locked=True, locked_until=datetime.now(timezone.utc) + timedelta(hours=1))
    assert evaluate(c, datetime.now(), 0).reason == LOCKED          # naive Zeit = Systemzone


def test_enforcer_lock_warns_then_logs_out_and_unlock_message():
    lg, nt, clk = FakeLogind(), FakeNotifier(), Clock()
    actions = []
    nt.notify_orig = nt.notify

    async def notify(user, uid, title, body, critical=False, on_action=None):
        actions.append(on_action)
        await nt.notify_orig(user, uid, title, body, critical)
    nt.notify = notify
    e = Enforcer(lg, nt, clk, on_request=lambda u: None)
    s = [sess("1")]
    asyncio.run(e.step(DAY, s, {"max": Decision(False, 0, LOCKED)}))
    assert nt.sent[0][1] == "Gesperrt" and "Eltern" in nt.sent[0][2] and nt.sent[0][3]
    assert actions[0] is None                                       # kein „Verlängerung anfordern“
    asyncio.run(e.step(DAY, s, {"max": Decision(True, 3600)}))     # vor Ablauf freigegeben
    assert nt.sent[1][1] == "Wieder freigegeben" and lg.terminated == []


def _enforcer():
    lg, nt, clk = FakeLogind(), FakeNotifier(), Clock()
    return Enforcer(lg, nt, clk), lg, nt, clk


def test_long_grace_warns_reminds_then_logs_out():
    e, lg, nt, clk = _enforcer()
    s = [sess("1")]
    d = Decision(False, 0, LOCKED, grace_seconds=600)
    asyncio.run(e.step(DAY, s, {"max": d}))
    assert "Abmeldung in 10 Minuten" in nt.sent[0][2]
    clk.t += 500
    asyncio.run(e.step(DAY, s, {"max": d}))
    assert len(nt.sent) == 1                                        # noch keine Erinnerung
    clk.t += 50                                                      # 50 s vor Ende
    asyncio.run(e.step(DAY, s, {"max": d}))
    asyncio.run(e.step(DAY, s, {"max": d}))
    assert [x[2] for x in nt.sent[1:]] == ["Abmeldung in 1 Minute – bitte jetzt speichern."]
    assert lg.terminated == []
    clk.t += 51
    asyncio.run(e.step(DAY, s, {"max": d}))
    assert lg.terminated == ["1"]


def test_zero_grace_logs_out_immediately():
    e, lg, nt, _ = _enforcer()
    asyncio.run(e.step(DAY, [sess("1")], {"max": Decision(False, 0, LOCKED, grace_seconds=0)}))
    assert "jetzt abgemeldet" in nt.sent[0][2] and lg.terminated == ["1"]


def test_grace_from_policy():
    d = evaluate(child(locked=True, lock_grace_seconds=300), NOW, 0)
    assert d.reason == LOCKED and d.grace_seconds == 300
    assert evaluate(child(), NOW, 10 ** 6).grace_seconds is None   # Zeitende: Standard-Countdown
