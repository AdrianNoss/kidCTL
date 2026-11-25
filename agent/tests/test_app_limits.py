"""Limit je Programm/Kategorie (K4) im Agenten: Zuordnung, Zählung (parallel geöffnete Programme einmal), Vorwarnung
und Schließen, geräteübergreifend/offline, Tageswechsel, Tray-Anzeige, Linux und Windows."""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

from kidctl_common import applimits as al
from kidctl_common import protocol as p
from kidctl_common import tray as t

from kidctl_agent.accounting import Usage
from kidctl_agent.gamelimit import AppLimits
from kidctl_agent.win.programs import key_norm
from test_free_apps import DAY, FIREFOX, GCOMPRIS, _agent, _winagent
from test_programs import proc
from test_windows_games import proc as winproc

CHROMIUM_HOME = "/home/kind/Downloads/chrome-linux/chromium"   # portabel, ohne Katalog-Eintrag


class Desk:
    def __init__(self):
        self.sent = []

    async def notify(self, user, uid, title, body, critical=False, on_action=None, action=None):
        self.sent.append((title, body, critical, on_action))


def _limits(a, *limits):
    a.policy.children[0].app_limits = [p.AppLimit(key=k, minutes=m, name=n) for k, m, n in limits]


# ---------- Zuordnung (gemeinsam) ----------

def test_matcher():
    cats = {FIREFOX: ["Network", "WebBrowser"], "exe:/usr/bin/discord": ["Network", "InstantMessaging"],
            "exe:/usr/bin/vlc": ["AudioVideo", "Player"], GCOMPRIS: ["Education"]}
    m = al.matcher(["cat:browser", "cat:chat", "cat:media", GCOMPRIS, p.STEAM_KEY], cats)
    assert m(FIREFOX, "/usr/lib/firefox/firefox") == {"cat:browser"}
    assert m(None, CHROMIUM_HOME) == {"cat:browser"}                 # am Namen, ohne Katalog
    assert m(None, "/home/kind/x", browser=True) == {"cat:browser"}  # Plattform erkennt Browser
    assert m("exe:/usr/bin/discord", "/usr/bin/discord") == {"cat:chat"}
    assert m("exe:/usr/bin/vlc", "/usr/bin/vlc") == {"cat:media"}
    assert m(GCOMPRIS, "/usr/bin/gcompris-qt") == {GCOMPRIS}
    assert m("flatpak:com.valvesoftware.Steam", "/app/bin/steam") == {p.STEAM_KEY}   # jede Steam-Variante
    assert m(None, "/usr/bin/kwin_wayland") == set()
    # Windows: Schlüssel ohne Groß-/Kleinschreibung, Browser am Namen der .exe
    edge = r"exe:C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    upper = "exe:" + edge[4:].upper()
    w = al.matcher([upper], {}, key_norm)
    assert w(edge, edge[4:]) == {upper}
    assert al.matcher(["cat:browser"], {})(None, r"C:\Users\kind\Downloads\chrome-win\chrome.exe") == {"cat:browser"}
    assert al.matcher([], cats)(FIREFOX, "/usr/lib/firefox/firefox") == set()


def test_old_policy_and_tray_without_fields():
    assert p.ChildPolicy.model_validate({"id": 1, "name": "x", "rules": []}).app_limits == []
    assert p.UsageUpdate.model_validate({"type": "usage_update", "child_id": 1, "day": "2026-11-25",
                                         "others_seconds": 0}).others_limit_seconds == {}
    assert t.TrayState.model_validate({"type": "state", "restricted": True}).limits == []


# ---------- Linux: Zählung ----------

def test_linux_parallel_browsers_count_once(monkeypatch, tmp_path):
    running = {1: proc("/usr/lib/firefox/firefox", pid=1), 2: proc(CHROMIUM_HOME, pid=2),
               3: proc("/usr/lib/firefox/firefox", pid=3)}   # zweiter Firefox-Prozess
    a, me = _agent(monkeypatch, tmp_path, [], running)
    _limits(a, ("cat:browser", 60, ""))
    a._count_apps({me}, DAY, 5.0)
    a._count_apps({me}, DAY, 5.0)
    assert a.usage.limits[(me, DAY.isoformat())] == {"cat:browser": 10.0}
    assert a.limit_running == {me: {"cat:browser"}}
    # Programm und Kategorie zugleich begrenzt -> beide zählen
    _limits(a, ("cat:browser", 60, ""), (FIREFOX, 30, "Firefox"))
    a._count_apps({me}, DAY, 5.0)
    assert a.usage.limits[(me, DAY.isoformat())] == {"cat:browser": 15.0, FIREFOX: 5.0}
    # nichts offen -> nichts läuft
    running.clear()
    a._count_apps({me}, DAY, 5.0)
    assert a.limit_running == {} and a.usage.limits[(me, DAY.isoformat())]["cat:browser"] == 15.0


def test_linux_free_app_with_limit(monkeypatch, tmp_path):
    """„Zählt nicht“ + eigenes Limit: keine Bildschirmzeit, aber Zeit fürs Limit."""
    a, me = _agent(monkeypatch, tmp_path, [GCOMPRIS], {1: proc("/usr/bin/gcompris-qt", pid=1),
                                                         2: proc("/usr/bin/kidctl-tray", pid=2)})
    _limits(a, (GCOMPRIS, 20, "GCompris"), ("cat:media", 30, ""))
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == {me}
    assert a.usage.limits[(me, DAY.isoformat())] == {GCOMPRIS: 5.0}   # KidCTL-Tray zählt nirgends


def test_linux_limit_closes_programs(monkeypatch, tmp_path):
    running = {1: proc("/usr/lib/firefox/firefox", pid=1, start=11), 2: proc(CHROMIUM_HOME, pid=2, start=12),
               3: proc("/usr/bin/gcompris-qt", pid=3, start=13)}
    a, me = _agent(monkeypatch, tmp_path, [], running)
    _limits(a, ("cat:browser", 10, ""))
    desk, killed, clock = Desk(), [], [100.0]
    a.applimits = AppLimits(desk, kill=lambda pid, sig: killed.append((pid, sig)), clock=lambda: clock[0],
                            lang_of=lambda u: "de")

    async def enforce():
        procs = a._limit_processes(1001, me) if a.limit_remaining(me, DAY, a.app_limits(me)[0]) <= 0 else {}
        await a.enforce_limits(me, 1001, DAY, a.limit_running.get(me, set()), procs)

    async def main():
        a.usage.add_limits(me, DAY, {"cat:browser"}, 5 * 60 - 1)   # noch 5 min
        a._count_apps({me}, DAY, 1.0)
        await enforce()
        assert desk.sent[-1][0] == "Browser" and "Noch 5 Minuten für Browser" in desk.sent[-1][1]
        assert desk.sent[-1][3] is None                               # kein Anfrageknopf
        a.usage.add_limits(me, DAY, {"cat:browser"}, 5 * 60)
        await enforce()
        assert killed == [(1, signal.SIGTERM), (2, signal.SIGTERM)]   # nur Browser, GCompris bleibt
        assert desk.sent[-1][0] == "Zeit für Browser aufgebraucht"
        clock[0] += 11
        await enforce()
        assert killed[2:] == [(1, signal.SIGKILL), (2, signal.SIGKILL)]
        # am nächsten Tag wieder frei
        tomorrow = DAY + timedelta(days=1)
        assert a.limit_remaining(me, tomorrow, a.app_limits(me)[0]) == 600
    asyncio.run(main())


def test_linux_tray_and_report(monkeypatch, tmp_path):
    a, me = _agent(monkeypatch, tmp_path, [], {1: proc("/usr/lib/firefox/firefox", pid=1)})
    _limits(a, ("cat:browser", 60, ""), (GCOMPRIS, 30, "GCompris"))
    today = a.now_local().date()
    a.usage.add_limits(me, today, {"cat:browser"}, 600)
    a.usage.others_limits[(1, today.isoformat())] = {"cat:browser": 300}
    a.langs.lang_of = lambda user: "en"
    st = a.tray_state(me)
    assert [(lim.name, lim.minutes, lim.used_seconds) for lim in st.limits] == [("Browser", 60, 900),
                                                                               ("GCompris", 30, 0)]
    a.policy.children[0].app_limits[0].key = "cat:media"
    assert a.tray_state(me).limits[0].name == "Music & video"
    # Bericht: Tag nur mit Limit-Zeit (z. B. nur „zählt nicht“-Programm mit Limit) wird trotzdem gemeldet
    u = Usage()
    u.add_limits(me, today, {GCOMPRIS}, 30)
    assert u.report_entries(today) == [(me, today, 0, 0)] and u.limit_entries(me, today) == {GCOMPRIS: 30}


def test_usage_persisted_offline(monkeypatch, tmp_path):
    """Offline: Zählerstände und zuletzt gemeldete andere Geräte überleben einen Neustart; alte Tage verfallen."""
    from kidctl_agent import store
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    u = Usage()
    u.add_limits("max", DAY, {"cat:browser", GCOMPRIS}, 120.5)
    u.others_limits[(1, DAY.isoformat())] = {"cat:browser": 60}
    u.save()
    v = Usage.load()
    assert v.used_limit_seconds(1, ["max"], DAY, "cat:browser") == 180
    assert v.used_limit_seconds(1, ["max"], DAY, GCOMPRIS) == 120
    v.prune(DAY + timedelta(days=30))
    assert v.limits == {} and v.others_limits == {}


def test_usage_update_sets_others(monkeypatch, tmp_path):
    a, me = _agent(monkeypatch, tmp_path, [], {})
    asyncio.run(a.on_message(p.UsageUpdate(child_id=1, day=DAY, others_seconds=0,
                                           others_limit_seconds={"cat:browser": 1200})))
    _limits(a, ("cat:browser", 30, ""))
    assert a.limit_remaining(me, DAY, a.app_limits(me)[0]) == 600


def test_app_limit_texts_english():
    desk = Desk()
    lim = AppLimits(desk, lang_of=lambda u: "en")

    async def main():
        await lim.step("x", lambda u="": "Discord", "max", 1, DAY, 60, True, [])
        await lim.step("x", lambda u="": "Discord", "max", 1, DAY, 0, True, [(5, 1)])
    lim.kill = lambda pid, sig: None
    lim.by_key.clear()
    asyncio.run(main())
    assert desk.sent[0][1] == "1 minute left for Discord – it will be closed afterwards. Please save."
    assert desk.sent[1][0] == "Time for Discord used up"


# ---------- Windows ----------

def test_winagent_limit(tmp_path, monkeypatch):
    edge = r"exe:C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    table = {50: winproc(r"C:\Program Files\GCompris\gcompris-qt.exe", pid=50),
             51: winproc(edge[4:], pid=51),
             52: winproc(r"C:\Users\kind\Downloads\chrome-win\chrome.exe", pid=52)}   # portabel, ohne Katalog
    agent, clock = _winagent(tmp_path, monkeypatch, table, [])
    agent.policy.children[0].app_limits = [p.AppLimit(key="cat:browser", minutes=1)]
    agent.applimits.clock = lambda: clock[0]
    desk = Desk()
    agent.applimits.desktop = desk

    async def main():
        await agent.tick_once()
        for _ in range(6):
            clock[0] += 5
            await agent.tick_once()
        today = agent.now_local().date()
        assert agent.usage.used_limit_seconds(1, ["lisa"], today, "cat:browser") == 30   # parallel einmal
        assert agent.tray_state("lisa").limits[0].used_seconds == 30
        assert any("Noch 1 Minute für Browser" in body for _t, body, _c, _a in desk.sent)
        for _ in range(7):
            clock[0] += 5
            await agent.tick_once()
        assert sorted(set(agent.programs.procs.killed)) == [51, 52]   # GCompris bleibt
    asyncio.run(main())
