"""Spiele-Limit: Regeln, Zählung, Durchsetzung (Vorwarnung, Schließen), Agent-Anbindung, Anfrage."""

import asyncio
import signal
from datetime import date, time

from kidctl_common import protocol as p
from kidctl_common import tray as t

from kidctl_agent.accounting import Usage
from kidctl_agent.agent import _is_launcher
from kidctl_agent.gamelimit import KILL_AFTER, GameLimit
from kidctl_agent.rules import game_limit
from test_tray import ME, connect, make_agent

DAY = date(2026, 10, 19)  # Montag


class Desk:
    def __init__(self):
        self.sent = []

    async def notify(self, user, uid, title, body, critical=False, on_action=None, action=None):
        self.sent.append((title, body, critical, action))


def child(game=60, extra=0):
    return p.ChildPolicy(id=1, name="M", rules=[p.DayRule(weekday=d, quota_minutes=120, window_start=time(0),
                                                          window_end=time(23, 59), game_minutes=game)
                                                for d in range(7)],
                         exceptions=[p.DayOverride(day=DAY, extra_game_minutes=extra)] if extra else [])


def test_game_limit_rule():
    assert game_limit(child(None), DAY) is None
    assert game_limit(child(60), DAY) == 3600
    assert game_limit(child(60, extra=15), DAY) == 75 * 60
    assert game_limit(child(0), DAY) == 0


def test_usage_counts_games_separately():
    u = Usage()
    u.tick(set(), DAY, now=0)
    u.tick({"lea"}, DAY, now=5)
    u.add_games("lea", DAY, 5)
    u.others_games[(1, DAY.isoformat())] = 100
    assert u.used_seconds(1, ["lea"], DAY) == 5 and u.used_game_seconds(1, ["lea"], DAY) == 105
    assert u.report_entries(DAY) == [("lea", DAY, 5, 5)]


def test_warn_then_close():
    async def scenario():
        desk, killed, clock = Desk(), [], [0.0]
        g = GameLimit(desk, kill=lambda pid, sig: killed.append((pid, sig)), clock=lambda: clock[0])
        await g.step("lea", 1001, DAY, None, True, [(7, 1)])                 # kein Limit: nichts
        await g.step("lea", 1001, DAY, 20 * 60, True, [])
        await g.step("lea", 1001, DAY, 299, False, [])                         # kein Spiel offen: keine Warnung
        assert desk.sent == []
        await g.step("lea", 1001, DAY, 299, True, [])
        await g.step("lea", 1001, DAY, 59, True, [])
        await g.step("lea", 1001, DAY, 50, True, [])
        assert [s[0] for s in desk.sent] == ["Spielzeit", "Spielzeit"] and desk.sent[1][2] is True
        assert "Noch 1 Minute Spielzeit" in desk.sent[1][1] and desk.sent[1][3] == "Mehr Spielzeit anfragen"

        await g.step("lea", 1001, DAY, 0, True, [(7, 1), (8, 1)])
        assert killed == [(7, signal.SIGTERM), (8, signal.SIGTERM)] and desk.sent[-1][0] == "Spielzeit aufgebraucht"
        clock[0] = KILL_AFTER + 1
        await g.step("lea", 1001, DAY, 0, True, [(8, 1)])                      # 7 beendet, 8 hängt
        assert killed[-1] == (8, signal.SIGKILL)
        clock[0] = KILL_AFTER + 5
        await g.step("lea", 1001, DAY, 0, False, [(9, 1)])                     # neu gestartet: gleich schließen
        assert killed[-1] == (9, signal.SIGTERM) and len(desk.sent) == 3       # Meldung gedrosselt

        await g.step("lea", 1001, DAY, 30 * 60, True, [])                      # Gutschrift: Warnungen wieder scharf
        await g.step("lea", 1001, DAY, 4 * 60, True, [])
        assert desk.sent[-1][0] == "Spielzeit"
    asyncio.run(scenario())


def test_launcher_is_not_a_game():
    assert _is_launcher("exe:/usr/bin/steam") and _is_launcher("exe:/usr/lib/steam/steam")
    assert _is_launcher("exe:/usr/bin/heroic") and not _is_launcher("exe:/usr/bin/supertuxkart")


def _with_limit(a, game=30, extra=0):
    c = a.policy.children[0]
    rules = [r.model_copy(update={"game_minutes": game}) for r in c.rules]
    exc = [p.DayOverride(day=a.now_local().date(), extra_game_minutes=extra)] if extra else []
    a.policy = a.policy.model_copy(update={"children": [c.model_copy(update={"rules": rules, "exceptions": exc})]})


def test_agent_remaining_state_and_request():
    async def scenario():
        a, desk = make_agent(connected=True)
        today = a.now_local().date()
        a.game_keys = {"exe:/usr/bin/supertuxkart"}
        assert a.is_game("game:steam:620") and a.is_game("exe:/usr/bin/supertuxkart") and not a.is_game("exe:/x")
        assert a.game_remaining(ME, today) is None
        _with_limit(a, 30, extra=10)
        a.usage.add_games(ME, today, 600)
        assert a.game_remaining(ME, today) == 40 * 60 - 600
        st = a.tray_state(ME)
        assert st.game_limit_minutes == 40 and st.game_used_seconds == 600

        c = await connect(a)
        await c.send(t.TrayHello())
        await c.recv()
        await c.recv()
        await c.send(t.TrayExtensionRequest(minutes=0, reason="x", target="games"))
        assert isinstance(await c.recv(skip_state=True), t.TrayError)
        await c.send(t.TrayExtensionRequest(minutes=15, window_end=time(22), reason="Level", target="games"))
        assert (await c.recv(skip_state=True)).kind == "extension_request"
        req = [m for m in a.conn.sent if isinstance(m, p.ExtensionRequest)][-1]
        assert (req.target, req.minutes, req.window_end) == ("games", 15, None)

        await a.open_game_request_dialog(ME)
        dlg = await c.recv(skip_state=True)
        assert isinstance(dlg, t.TrayOpenRequestDialog) and dlg.target == "games"
        await a.tray.stop()

        await a.on_message(p.UsageUpdate(child_id=1, day=today, others_seconds=0, others_game_seconds=300))
        assert a.game_remaining(ME, today) == 40 * 60 - 900
    asyncio.run(scenario())


def test_game_decision_text():
    async def scenario():
        from test_tray import chat_msg
        a, desk = make_agent()
        a._sessions = await a.logind.sessions()
        await a.on_chat_message(chat_msg(1))
        kw = dict(from_child=True, kind="extension_request", request_target="games", requested_minutes=20)
        await a.on_chat_message(chat_msg(2, status="pending", **kw))
        await a.on_chat_message(chat_msg(2, status="approved", approved_minutes=15, **kw))
        assert desk.sent[-1] == (ME, "Spielzeit verlängert", "+15 Minuten")
    asyncio.run(scenario())


def test_agent_closes_games_when_exhausted(monkeypatch):
    async def scenario():
        a, desk = make_agent()
        today = a.now_local().date()
        _with_limit(a, 10)
        a.usage.add_games(ME, today, 600)
        killed = []
        a.gamelimit.kill = lambda pid, sig: killed.append((pid, sig))
        monkeypatch.setattr(a, "_game_processes", lambda uid, user: [(4242, 1)])
        sessions = await a.logind.sessions()
        decisions = a.decisions(a.now_local())
        await a._enforce_games(today, sessions, decisions)
        assert killed == [(4242, signal.SIGTERM)]
        assert desk.sent[-1][1] == "Spielzeit aufgebraucht"
        # Bildschirmzeit vorbei: das regelt der Enforcer (Abmeldung), nicht das Spiele-Limit
        killed.clear()
        await a._enforce_games(today, sessions, {})
        assert killed == []
    asyncio.run(scenario())
