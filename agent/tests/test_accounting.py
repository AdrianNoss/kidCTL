import os
import stat
from datetime import date

from kidctl_agent import store
from kidctl_agent.accounting import MAX_GAP, Usage

DAY = date(2026, 10, 13)


def test_tick_counts_once_per_user_and_ignores_gaps():
    u = Usage()
    assert u.tick({"max"}, DAY, now=100.0) == 0          # erster Takt: nur Startpunkt
    assert u.tick({"max", "lena"}, DAY, now=105.0) == 5.0
    assert u.tick({"max"}, DAY, now=105.0 + MAX_GAP + 1) == 0  # Suspend/Lücke
    assert u.tick({"max"}, DAY, now=150.0 + MAX_GAP) == 0     # Rückwärts/Lücke
    u.tick({"max"}, DAY, now=160.0 + MAX_GAP)
    assert u.local[("max", DAY.isoformat())] == 15.0
    assert u.local[("lena", DAY.isoformat())] == 5.0


def test_used_seconds_sums_users_and_other_devices():
    u = Usage()
    u.local[("max", DAY.isoformat())] = 600.4
    u.local[("max2", DAY.isoformat())] = 100.0
    u.others[(1, DAY.isoformat())] = 900
    assert u.used_seconds(1, ["max", "max2"], DAY) == 1600
    assert u.used_seconds(2, ["lena"], DAY) == 0


def test_persistence_roundtrip_and_permissions(state_dir):
    u = Usage()
    u.local[("max", DAY.isoformat())] = 42.0
    u.others[(1, DAY.isoformat())] = 7
    u.save()
    f = store.path(store.USAGE_FILE)
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(state_dir).st_mode) == 0o700
    v = Usage.load()
    assert v.local == u.local and v.others == u.others


def test_prune_and_report_entries():
    u = Usage()
    for d in ("2026-10-01", "2026-10-12", "2026-10-13"):
        u.local[("max", d)] = 60.0
    u.prune(DAY)
    assert ("max", "2026-10-01") not in u.local
    assert [e[1].isoformat() for e in u.report_entries(DAY)] == ["2026-10-12", "2026-10-13"]
