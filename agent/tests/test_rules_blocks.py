"""Sperrzeiten und Fenster über Mitternacht (Protokoll 2)."""

from datetime import date, datetime, time, timedelta

from kidctl_common.protocol import Block, ChildPolicy, DayOverride, DayRule

from kidctl_agent.rules import BLOCKED, QUOTA, WINDOW_CLOSED, WINDOW_NOT_OPEN, evaluate

FRI = date(2026, 10, 16)  # Freitag (weekday 4)
SAT = FRI + timedelta(days=1)


def child(quota=600, start=time(8), end=time(20), blocks=(), exceptions=(), rules=None):
    rules = rules or {}
    return ChildPolicy(id=1, name="Max", exceptions=list(exceptions), rules=[
        rules.get(d) or DayRule(weekday=d, quota_minutes=quota, window_start=start, window_end=end,
                                blocks=list(blocks)) for d in range(7)])


def at(day, h, m=0):
    return datetime.combine(day, time(h, m))


SCHOOL = Block(start=time(8), end=time(13), name="Schule")
DINNER = Block(start=time(18), end=time(19))


def test_block_denies_with_name_and_until():
    d = evaluate(child(blocks=[SCHOOL]), at(FRI, 9), 0)
    assert not d.allowed and d.reason == BLOCKED
    assert d.block == SCHOOL


def test_remaining_ends_at_next_block():
    d = evaluate(child(blocks=[SCHOOL, DINNER]), at(FRI, 17, 30), 0)
    assert d.allowed and d.remaining_seconds == 30 * 60


def test_after_block_allowed_again():
    c = child(blocks=[DINNER])
    assert evaluate(c, at(FRI, 19), 0).allowed
    assert evaluate(c, at(FRI, 18, 59), 0).reason == BLOCKED


def test_block_outside_frame_has_no_effect():
    c = child(blocks=[Block(start=time(21), end=time(22))])
    assert evaluate(c, at(FRI, 21, 30), 0).reason == WINDOW_CLOSED
    assert evaluate(c, at(FRI, 19, 50), 0).remaining_seconds == 10 * 60


def test_quota_still_limits_between_blocks():
    c = child(quota=60, blocks=[SCHOOL])
    assert evaluate(c, at(FRI, 14), 3600).reason == QUOTA
    assert evaluate(c, at(FRI, 14), 3000).remaining_seconds == 600


def test_window_over_midnight_before_midnight():
    c = child(start=time(16), end=time(1))
    d = evaluate(c, at(FRI, 23), 0)
    assert d.allowed and d.remaining_seconds == 2 * 3600   # bis 01:00 am Folgetag
    assert evaluate(c, at(FRI, 15, 59), 0).reason == WINDOW_NOT_OPEN


def test_window_over_midnight_carries_into_next_day():
    c = child(start=time(16), end=time(1))
    d = evaluate(c, at(SAT, 0, 30), 0)                      # Rahmen des Vortags, Kontingent des neuen Tags
    assert d.allowed and d.remaining_seconds == 30 * 60
    assert evaluate(c, at(SAT, 1), 0).reason == WINDOW_NOT_OPEN
    assert evaluate(c, at(SAT, 0, 30), 600 * 60).reason == QUOTA


def test_carry_over_only_from_crossing_previous_day():
    fri = DayRule(weekday=4, quota_minutes=600, window_start=time(8), window_end=time(20))
    sat = DayRule(weekday=5, quota_minutes=600, window_start=time(16), window_end=time(1))
    c = child(rules={4: fri, 5: sat})
    assert evaluate(c, at(SAT, 0, 30), 0).reason == WINDOW_NOT_OPEN


def test_previous_day_blocks_do_not_apply_after_midnight():
    c = child(start=time(16), end=time(2), blocks=[Block(start=time(0), end=time(6))])
    # Sperrzeit 0–6 Uhr des Samstags gilt im Samstagsrahmen (ab 16 Uhr) nicht; nach 0 Uhr zählt der Freitagsrahmen
    assert evaluate(c, at(SAT, 0, 30), 0).allowed


def test_override_end_over_midnight():
    c = child(exceptions=[DayOverride(day=FRI, window_end_override=time(0, 30))])
    assert evaluate(c, at(FRI, 23, 50), 0).remaining_seconds == 40 * 60
    assert evaluate(c, at(SAT, 0, 15), 0).allowed
    assert evaluate(c, at(SAT, 0, 30), 0).reason == WINDOW_NOT_OPEN


def test_v1_rule_without_blocks_unchanged():
    c = child()
    assert evaluate(c, at(FRI, 7), 0).reason == WINDOW_NOT_OPEN
    assert evaluate(c, at(FRI, 20), 0).reason == WINDOW_CLOSED
    assert evaluate(c, at(FRI, 19, 50), 0).remaining_seconds == 600


def test_dst_spring_forward_carry_over():
    """Zeitumstellung (29.03.2026, 02:00 -> 03:00): Fenster nach Wanduhr, Rahmen des Vortags bis 04:00."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Berlin")
    sun = date(2026, 3, 29)
    c = child(start=time(16), end=time(4))
    d = evaluate(c, datetime.combine(sun, time(3, 30), tzinfo=tz), 0)
    assert d.allowed and d.remaining_seconds == 30 * 60
    assert evaluate(c, datetime.combine(sun, time(4), tzinfo=tz), 0).reason == WINDOW_NOT_OPEN


def test_dst_fall_back_block():
    """Zeitumstellung (25.10.2026, 03:00 -> 02:00): Sperrzeit nach Wanduhr, auch in der doppelten Stunde."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Berlin")
    c = child(start=time(0), end=time(23), blocks=[Block(start=time(2), end=time(3), name="Nacht")])
    for fold in (0, 1):
        assert evaluate(c, datetime(2026, 10, 25, 2, 30, tzinfo=tz, fold=fold), 0).reason == BLOCKED


def test_cached_policy_with_block_enforced_offline(monkeypatch):
    """Offline: gecachte Policy (Protokoll 2) mit laufender Sperrzeit führt zur Abmeldungs-Warnung."""
    import asyncio

    from kidctl_common import protocol as p

    from kidctl_agent import agent as agent_mod
    from kidctl_agent import store
    from kidctl_agent.agent import Agent
    from test_agent import FakeConn, FakeLogind, FakeNotifier

    all_day = Block(start=time(0), end=time(23, 59, 59), name="Schule")
    pol = p.Policy(version="v2", timezone="Europe/Berlin", users={"kind": 1}, children=[
        child(start=time(0), end=time(23, 59, 59), blocks=[all_day])])
    store.save(store.POLICY_FILE, pol.model_dump(mode="json"))
    clock = [100.0]
    monkeypatch.setattr(agent_mod, "boottime", lambda: clock[0])
    nt = FakeNotifier()
    a = Agent(FakeLogind(), nt, {"server_url": "https://x", "device_id": 1, "token": "t"})
    a.conn = FakeConn()

    async def scenario():
        clock[0] += agent_mod.STARTUP_SYNC
        await a.tick_once()
        assert nt.sent == ["Bildschirmzeit zu Ende"]
    asyncio.run(scenario())
