"""„Zählt nicht“-Programme im Agenten: Linux/Windows-Prozessprüfung (Umgehungsversuche) und Zählung."""

from __future__ import annotations

import asyncio
import getpass
from dataclasses import replace
from types import SimpleNamespace

from kidctl_common import protocol as p
from kidctl_common import tray as t

from kidctl_agent import agent as agent_mod
from kidctl_agent import apptime, store
from kidctl_agent.apptime import AppIndex, free_process_ok
from kidctl_agent.win import apptime as winapptime
from kidctl_agent.win import catalog
from test_programs import proc
from test_windows_games import Procs
from test_windows_games import proc as winproc

from datetime import date

DAY = date(2026, 11, 25)
GCOMPRIS = "exe:/usr/bin/gcompris-qt"
WRITER = "exe:/usr/lib/libreoffice/program/soffice.bin"
FIREFOX = "exe:/usr/lib/firefox/firefox"


def _root_owned(monkeypatch, owners=None):
    owners = owners or {}
    monkeypatch.setattr(apptime, "_stat", lambda path: SimpleNamespace(st_uid=owners.get(path, 0)))


# ---------- Linux: Prozessprüfung ----------

def test_linux_free_process_ok(monkeypatch):
    _root_owned(monkeypatch, {"/usr/bin/kopie": 1001})
    assert free_process_ok(proc("/usr/bin/gcompris-qt"))
    # Browser/Terminal/Interpreter im Ordner der App (z. B. mitgelieferter Browser) gilt nie als ausgenommen
    assert not free_process_ok(proc("/usr/lib/libreoffice/program/firefox"))
    assert not free_process_ok(proc("/usr/lib/libreoffice/program/konsole"))
    assert not free_process_ok(proc("/usr/bin/python3.13", argv=("python3.13", "-c", "x")))
    # Datei gehört nicht root (vom Kind angelegt) -> nicht ausgenommen
    assert not free_process_ok(proc("/usr/bin/kopie"))
    # Flatpak: nur Cgroup-Name (systemd-run --user --scope --unit=app-flatpak-<id>-1.scope firefox) reicht nicht
    fake_scope = proc("/usr/lib/firefox/firefox", flatpak="org.kde.gcompris")
    assert not free_process_ok(fake_scope)
    real = replace(proc("/app/bin/gcompris-qt", flatpak="org.kde.gcompris"), mnt_foreign=True)
    assert free_process_ok(real)
    # Container (eigener Mount-Namensraum ohne Flatpak) -> nein
    cont = replace(proc("/usr/bin/gcompris-qt"), mnt_foreign=True)
    assert not free_process_ok(cont)
    # Electron mit Optionen, die fremden Code laden
    assert not free_process_ok(proc("/usr/lib/electron33/electron",
                                    argv=("/usr/lib/electron33/electron", "-e", "require('child_process')",
                                          "/usr/lib/lernapp/app.asar")))


def _agent(monkeypatch, tmp_path, free_apps, running, apps=None):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    me = getpass.getuser()
    monkeypatch.setattr(agent_mod, "pids_of", lambda uids: list(running))
    monkeypatch.setattr(agent_mod, "read_proc", lambda pid: running.get(pid))
    monkeypatch.setattr(agent_mod, "read_environ", lambda pid: {})
    _root_owned(monkeypatch)
    a = agent_mod.Agent(logind=None, desktop=None)
    a.is_admin_user = lambda user: False
    a.apps = apps or [p.AppEntry(key=GCOMPRIS, name="GCompris", categories=["Education"]),
                      p.AppEntry(key=WRITER, name="Writer", categories=["Office"]),
                      p.AppEntry(key=FIREFOX, name="Firefox", categories=["Network", "WebBrowser"]),
                      p.AppEntry(key="exe:/usr/bin/supertux2", name="SuperTux", categories=["Game"]),
                      p.AppEntry(key="exe:/usr/bin/kidctl-tray", name="KidCTL", categories=["Utility"])]
    a.game_keys = {"exe:/usr/bin/supertux2"}
    a.app_index = AppIndex({x.key for x in a.apps})
    a.policy = p.Policy(version="v", timezone="Europe/Berlin", users={me: 1}, children=[p.ChildPolicy(
        id=1, name="Kind", free_apps=free_apps,
        rules=[p.DayRule(weekday=d, quota_minutes=60, window_start="00:00", window_end="23:59") for d in range(7)])])
    return a, me


def test_linux_only_free_apps_do_not_count(monkeypatch, tmp_path):
    running = {1: proc("/usr/bin/gcompris-qt", pid=1), 2: proc("/usr/lib/libreoffice/program/soffice.bin", pid=2),
               3: proc("/usr/bin/kidctl-tray", pid=3),       # KidCTL-Tray läuft immer, stört nicht
               4: proc("/usr/bin/kwin_wayland", pid=4)}       # Systemprozess ohne Katalog-Eintrag
    a, me = _agent(monkeypatch, tmp_path, [GCOMPRIS, WRITER], running)
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == {me}
    assert a.apptime.local[(me, DAY.isoformat(), GCOMPRIS)] == 5.0     # Programmzeit zählt weiter
    assert a.tray_state(me).free

    # zusätzlich Firefox (Katalog) -> zählt
    running[5] = proc("/usr/lib/firefox/firefox", pid=5)
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set() and not a.tray_state(me).free
    # Browser ohne Katalog-Eintrag (entpackt im Home) -> zählt ebenfalls
    running[5] = proc("/home/kind/Downloads/firefox/firefox", pid=5)
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()
    # Spiel -> zählt
    running[5] = proc("/usr/bin/supertux2", pid=5)
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()
    # ohne gezählten Takt (Lücke) -> kein Freibetrag aus einem alten Stand
    del running[5]
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == {me}
    asyncio.run(a._after_count(SimpleNamespace(date=lambda: DAY), {me}, 0.0))
    assert a.free_now == set()


def test_linux_nothing_open_counts(monkeypatch, tmp_path):
    a, me = _agent(monkeypatch, tmp_path, [GCOMPRIS], {4: proc("/usr/bin/kwin_wayland", pid=4)})
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()


def test_linux_policy_cannot_exempt_browser_or_game(monkeypatch, tmp_path):
    """Manipulierte/veraltete Policy nennt Firefox, Konsole und ein Spiel als „zählt nicht“ -> Agent ignoriert das."""
    running = {5: proc("/usr/lib/firefox/firefox", pid=5)}
    a, me = _agent(monkeypatch, tmp_path, [FIREFOX, "exe:/usr/bin/supertux2", "exe:/usr/bin/konsole"], running)
    assert a.free_keys(me) == set()
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()
    running = {5: proc("/usr/bin/supertux2", pid=5)}
    monkeypatch.setattr(agent_mod, "pids_of", lambda uids: list(running))
    monkeypatch.setattr(agent_mod, "read_proc", lambda pid: running.get(pid))
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()
    # Kategorie WebBrowser im eigenen Katalog genügt (Name allein unauffällig)
    a.apps.append(p.AppEntry(key="exe:/usr/bin/lernbrowser", name="Lernbrowser", categories=["Education", "WebBrowser"]))
    a.policy.children[0].free_apps.append("exe:/usr/bin/lernbrowser")
    assert "exe:/usr/bin/lernbrowser" not in a.free_keys(me)


def test_linux_renamed_browser_in_free_app_dir(monkeypatch, tmp_path):
    """Umgehungsversuch: Browser-Binary unter dem Ordner der Lern-App (Ordner-Zuordnung) -> zählt."""
    running = {1: proc("/opt/lernapp/firefox", pid=1)}
    a, me = _agent(monkeypatch, tmp_path, ["exe:/opt/lernapp/lernapp"], running,
                   apps=[p.AppEntry(key="exe:/opt/lernapp/lernapp", name="Lernapp", categories=["Education"])])
    assert a.app_index.key_for(running[1]) == "exe:/opt/lernapp/lernapp"
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()
    # Kopie im Home mit dem Namen der Lern-App: gehört dem Kind -> nicht ausgenommen
    running = {1: proc("/opt/lernapp/lernapp", pid=1)}
    monkeypatch.setattr(agent_mod, "read_proc", lambda pid: running.get(pid))
    monkeypatch.setattr(agent_mod, "pids_of", lambda uids: list(running))
    _root_owned(monkeypatch, {"/opt/lernapp/lernapp": 1001})
    a._count_apps({me}, DAY, 5.0)
    assert a.free_now == set()


def test_old_policy_without_field():
    pol = p.ChildPolicy.model_validate({"id": 1, "name": "x", "rules": []})
    assert pol.free_apps == []
    assert t.TrayState.model_validate({"type": "state", "restricted": True}).free is False


# ---------- Windows ----------

def test_windows_free_process_ok():
    assert winapptime.free_process_ok(winproc(r"C:\Program Files\GCompris\gcompris-qt.exe"), original=lambda e: "")
    assert winapptime.free_process_ok(winproc(r"C:\Program Files\WindowsApps\OneNote\onenote.exe",
                                              pfn="Microsoft.Office.OneNote_8wekyb3d8bbwe"), original=lambda e: "")
    # im Benutzerordner (vom Kind beschreibbar) -> nie
    assert not winapptime.free_process_ok(winproc(r"C:\Users\kind\AppData\Local\Lernapp\lernapp.exe"),
                                          original=lambda e: "")
    # umbenannte chrome.exe: Versionsinformation verrät sie
    assert not winapptime.free_process_ok(winproc(r"C:\Program Files\Lernapp\lernapp.exe"),
                                          original=lambda e: "chrome.exe")
    assert not winapptime.free_process_ok(winproc(r"C:\Program Files\Lernapp\msedge.exe"), original=lambda e: "")
    assert not winapptime.free_process_ok(winproc(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
                                          original=lambda e: "")


def _winagent(tmp_path, monkeypatch, table, free_apps):
    from test_windows import FakeApi
    from kidctl_agent.win import agent as wa
    from kidctl_agent.win import api
    from kidctl_agent.win.accounts import Accounts
    from kidctl_agent.win.desktop import WinDesktop
    from kidctl_agent.win.sessions import WinSessions

    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(wa.Updater, "__init__", lambda self, device: setattr(self, "key", None) or None)
    monkeypatch.setattr(winapptime, "_original_name", lambda exe: "chrome.exe" if "kopie" in exe else "")
    clock = [1000.0]
    monkeypatch.setattr(wa, "boottime", lambda: clock[0])
    monkeypatch.setattr("kidctl_agent.accounting.boottime", lambda: clock[0])
    fake = FakeApi()
    acc = Accounts(fake)
    agent = wa.WinAgent(WinSessions(acc, fake), WinDesktop(fake, clock=lambda: clock[0]), None, acc)
    agent.langs.read = lambda sid: "de-DE"
    agent.tray = SimpleNamespace(send_to_user=lambda u, m: asyncio.sleep(0, 1), push_states=lambda now: asyncio.sleep(0),
                                 tray_icons=lambda: {}, clients=[])
    agent.enforcer.clock = lambda: clock[0]
    agent.gamelimit.clock = lambda: clock[0]
    agent.programs.catalog = catalog.Catalog(apps=[
        p.AppEntry(key=r"exe:C:\Program Files\GCompris\gcompris-qt.exe", name="GCompris"),
        p.AppEntry(key=r"exe:C:\Program Files\Lernapp\lernapp.exe", name="Lernapp"),
        p.AppEntry(key=r"exe:C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", name="Edge",
                   categories=["Network", "WebBrowser"])])
    agent.apps = agent.programs.catalog.apps
    agent.programs.names = {a.key: a.name for a in agent.programs.catalog.apps}
    agent.programs.procs = Procs(table)
    agent.policy = p.Policy(version="v", timezone="Europe/Berlin", users={"lisa": 1}, children=[{
        "id": 1, "name": "Lisa", "free_apps": free_apps,
        "rules": [{"weekday": d, "quota_minutes": 600, "window_start": "00:00", "window_end": "23:59"}
                  for d in range(7)]}])
    fake.sessions = [(2, api.WTSActive, "lisa", 1, 555)]
    return agent, clock


def test_winagent_free_app_does_not_use_screen_time(tmp_path, monkeypatch):
    gcompris = r"exe:C:\Program Files\GCompris\gcompris-qt.exe"
    table = {50: winproc(r"C:\Program Files\GCompris\gcompris-qt.exe", pid=50)}
    agent, clock = _winagent(tmp_path, monkeypatch, table, [gcompris])

    async def ticks(n):
        for _ in range(n):
            clock[0] += 5
            await agent.tick_once()

    async def main():
        await agent.tick_once()
        await ticks(12)
        today = agent.now_local().date()
        used = agent.usage.used_seconds(1, ["lisa"], today)
        assert used <= 5                                   # höchstens der erste Takt vor der Erkennung
        assert agent.tray_state("lisa").free
        assert {k for _u, _d, k, _s in agent.apptime.report_entries(today)} == {gcompris}   # Programmzeit zählt
        # Edge dazu -> Bildschirmzeit läuft
        table[51] = winproc(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", pid=51)
        await ticks(12)
        assert agent.usage.used_seconds(1, ["lisa"], today) >= used + 55
        # portabler Browser im Benutzerordner (nicht im Katalog) -> zählt ebenfalls
        table[51] = winproc(r"C:\Users\kind\Downloads\chrome-win\chrome.exe", pid=51)
        before = agent.usage.used_seconds(1, ["lisa"], today)
        await ticks(12)
        assert agent.usage.used_seconds(1, ["lisa"], today) >= before + 55
        del table[51]
        before = agent.usage.used_seconds(1, ["lisa"], today)
        await ticks(12)
        assert agent.usage.used_seconds(1, ["lisa"], today) <= before + 5
    asyncio.run(main())


def test_winagent_renamed_browser_counts(tmp_path, monkeypatch):
    """Umgehungsversuch: Kopie von chrome.exe unter dem Namen der Lern-App (Versionsinfo: chrome.exe)."""
    lernapp = r"exe:C:\Program Files\Lernapp\lernapp.exe"
    table = {50: winproc(r"C:\Program Files\Lernapp\kopie\lernapp.exe", pid=50)}
    agent, clock = _winagent(tmp_path, monkeypatch, table, [lernapp,
                                                            r"exe:C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"])

    async def main():
        await agent.tick_once()
        for _ in range(12):
            clock[0] += 5
            await agent.tick_once()
        assert agent.usage.used_seconds(1, ["lisa"], agent.now_local().date()) >= 55
        assert not agent.tray_state("lisa").free
    asyncio.run(main())
