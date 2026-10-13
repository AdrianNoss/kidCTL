from datetime import date, datetime, time

from kidctl_common.protocol import ChildPolicy, DayOverride, DayRule

from kidctl_agent.rules import QUOTA, WINDOW_CLOSED, WINDOW_NOT_OPEN, evaluate

TUE = date(2026, 10, 13)  # Dienstag (weekday 1)


def child(quota=60, start=time(8), end=time(20), exceptions=()):
    return ChildPolicy(id=1, name="Max", exceptions=list(exceptions), rules=[
        DayRule(weekday=d, quota_minutes=quota, window_start=start, window_end=end) for d in range(7)])


def at(h, m=0, s=0):
    return datetime.combine(TUE, time(h, m, s))


def test_window_and_quota():
    c = child()
    assert evaluate(c, at(7, 59), 0).reason == WINDOW_NOT_OPEN
    assert evaluate(c, at(20, 0), 0).reason == WINDOW_CLOSED
    d = evaluate(c, at(12), 600)
    assert d.allowed and d.remaining_seconds == 3000
    assert evaluate(c, at(12), 3600).reason == QUOTA
    assert evaluate(c, at(12), 9999).reason == QUOTA


def test_remaining_limited_by_window_end():
    d = evaluate(child(quota=120), at(19, 50), 0)
    assert d.allowed and d.remaining_seconds == 600


def test_exception_extra_minutes_and_window_override():
    c = child(exceptions=[DayOverride(day=TUE, extra_minutes=30, window_end_override=time(21))])
    assert evaluate(c, at(12), 3600).remaining_seconds == 1800       # 60 + 30 min
    assert evaluate(c, at(20, 30), 0).remaining_seconds == 1800      # Fenster bis 21:00
    other_day = child(exceptions=[DayOverride(day=date(2026, 10, 14), extra_minutes=30)])
    assert evaluate(other_day, at(12), 3600).reason == QUOTA          # Ausnahme gilt nur ihr Datum


def test_missing_rule_blocks():
    c = ChildPolicy(id=1, name="Max", rules=[])
    assert not evaluate(c, at(12), 0).allowed
