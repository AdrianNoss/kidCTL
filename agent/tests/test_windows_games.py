"""Spiele unter Windows: Steam/Epic erkennen, Launcher-Regel, Programm- und Spielzeit, Spiele-Limit."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from kidctl_common import protocol as p

from kidctl_agent import store
from kidctl_agent.win import catalog, games
from kidctl_agent.win.apptime import WinAppIndex
from kidctl_agent.win.procs import WinProc
from kidctl_agent.win.programs import WinAllowed, classify

KID = "S-1-5-21-1-2-3-1002"


def proc(exe, pid=100, pfn="", start=1, ppid=4):
    return WinProc(pid=pid, ppid=ppid, exe=exe, session=2, sid=KID, start=start, pfn=pfn)


STEAM = r"C:\Program Files (x86)\Steam"
LIB2 = r"D:\SteamLibrary"
EPIC = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests"
FILES = {
    STEAM + r"\steamapps\libraryfolders.vdf":
        '"libraryfolders"\n{\n "0"\n {\n  "path"  "C:\\\\Program Files (x86)\\\\Steam"\n }\n'
        ' "1"\n {\n  "path"  "D:\\\\SteamLibrary"\n }\n}\n',
    LIB2 + r"\steamapps\appmanifest_620.acf": '"AppState"\n{\n "appid"  "620"\n "name"  "Portal 2"\n "installdir"  "Portal 2"\n}\n',
    STEAM + r"\steamapps\appmanifest_1.acf": '"AppState" { "appid" "1" "installdir" "..\\\\evil" }',
    EPIC + r"\a.item": json.dumps({"AppName": "Fortnite", "DisplayName": "Fortnite",
                                     "InstallLocation": r"C:\Program Files\Epic Games\Fortnite"}),
    EPIC + r"\b.item": json.dumps({"AppName": "x; rm", "InstallLocation": r"C:\x"}),
}
GAME_EXE = LIB2 + r"\steamapps\common\Portal 2\portal2.exe"


def _listdir(path):
    names = [k[len(path) + 1:] for k in FILES if k.startswith(path + "\\") and "\\" not in k[len(path) + 1:]]
    if not names:
        raise OSError(path)
    return names


@pytest.fixture
def steam_tree():
    return games.scan(steam=STEAM, epic_dir=EPIC, listdir=_listdir, read=lambda p: FILES.get(p, ""))


def test_game_index(steam_tree):
    idx = steam_tree
    assert idx.game_of(GAME_EXE) == ("game:steam:620", "Portal 2")
    assert idx.game_of(r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\x.exe") == \
        ("game:epic:Fortnite", "Fortnite")
    assert idx.game_of(r"C:\Program Files\Other\x.exe") is None
    assert len(idx.games) == 2   # ungültiger installdir und AppName verworfen
    assert catalog.norm(LIB2 + r"\steamapps\common") in idx.steam_dirs


def test_launcher_rule(steam_tree):
    idx = steam_tree
    game_exe = GAME_EXE
    owner = lambda path: KID   # noqa: E731 – Steam lädt als Kind herunter
    blocked = WinAllowed(set(), sid=KID)
    assert classify(proc(game_exe), blocked, owner).code == "own_program"
    released = WinAllowed({"exe:" + idx.steam_exe}, sid=KID, launcher_dirs=tuple(idx.steam_dirs))
    assert classify(proc(game_exe), released, owner) is None
    # anderer Launcher: Nachfahren erlaubt
    bnet = r"C:\Program Files (x86)\Battle.net\Battle.net.exe"
    a = WinAllowed({"exe:" + bnet}, sid=KID)
    assert classify(proc(r"C:\Program Files (x86)\Overwatch\Overwatch.exe"), a, ancestors=lambda: [bnet]) is None
    assert classify(proc(r"C:\Program Files (x86)\Overwatch\Overwatch.exe"), a, ancestors=lambda: []).code == "not_allowed"
    assert classify(proc(r"C:\Y\y.exe"), WinAllowed({r"exe:C:\X\other.exe"}, sid=KID),
                    ancestors=lambda: [bnet]).code == "not_allowed"   # Launcher nicht freigegeben


def test_app_index(steam_tree):
    idx = steam_tree
    cat = catalog.Catalog(apps=[
        p.AppEntry(key=r"exe:C:\Program Files\Mozilla Firefox\firefox.exe", name="Firefox"),
        p.AppEntry(key=r"exe:C:\Users\kind\AppData\Local\Roblox\Versions\v1\RobloxPlayerLauncher.exe", name="Roblox"),
        p.AppEntry(key="appx:Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe", name="Solitaire",
                   categories=["Game"]),
        p.AppEntry(key=r"exe:C:\Windows\System32\cmd.exe", name="cmd (Shell)", categories=["Shell"])])
    ai = WinAppIndex(cat, idx)
    assert ai.key_for(proc(r"C:\Program Files\Mozilla Firefox\pingsender.exe"))[0].endswith("firefox.exe")
    assert ai.key_for(proc(r"C:\Users\kind\AppData\Local\Roblox\Versions\v2\RobloxPlayerBeta.exe"))[0].endswith(
        "RobloxPlayerLauncher.exe")
    assert ai.key_for(proc("x", pfn="Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe"))[0].startswith("appx:")
    assert ai.key_for(proc(GAME_EXE)) == ("game:steam:620", "Portal 2")
    assert ai.key_for(proc(r"C:\Windows\System32\cmd.exe")) is None and ai.key_for(proc(r"C:\Windows\explorer.exe")) is None


# ---------- WinAgent: Spielzeit, Spiele-Limit, „zählt als Spiel“ ----------

class Procs:
    def __init__(self, table):
        self.table, self.killed = table, []

    def snapshot(self):
        return {pid: 4 for pid in self.table}

    def read(self, pid, ppid=0):
        return self.table.get(pid)

    def terminate(self, pid):
        self.killed.append(pid)
        return True

    def file_owner(self, path):
        return ""


def test_winagent_game_time_and_limit(tmp_path, monkeypatch):
    from test_windows import FakeApi
    from kidctl_agent.win import agent as wa
    from kidctl_agent.win.accounts import Accounts
    from kidctl_agent.win.desktop import WinDesktop
    from kidctl_agent.win.sessions import WinSessions
    from kidctl_agent.win import api

    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(wa.Updater, "__init__", lambda self, device: setattr(self, "key", None) or None)
    clock = [1000.0]
    monkeypatch.setattr(wa, "boottime", lambda: clock[0])
    monkeypatch.setattr("kidctl_agent.accounting.boottime", lambda: clock[0])
    fake = FakeApi()
    acc = Accounts(fake)
    agent = wa.WinAgent(WinSessions(acc, fake), WinDesktop(fake, clock=lambda: clock[0]), None, acc)
    agent.langs.read = lambda sid: "de-DE"
    notes = []

    async def send(user, msg):
        notes.append(msg)
        return 1
    agent.tray = SimpleNamespace(send_to_user=send, push_states=lambda now: asyncio.sleep(0), tray_icons=lambda: {},
                                 clients=[])
    agent.enforcer.clock = lambda: clock[0]
    agent.gamelimit.clock = lambda: clock[0]
    roblox = r"exe:C:\Users\kind\AppData\Local\Roblox\Roblox.exe"
    agent.programs.catalog = catalog.Catalog(apps=[
        p.AppEntry(key="appx:Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe", name="Solitaire", categories=["Game"]),
        p.AppEntry(key="appx:Microsoft.GamingApp_8wekyb3d8bbwe", name="Xbox", categories=["Game"]),
        p.AppEntry(key=roblox, name="Roblox")])
    agent.programs.names = {a.key: a.name for a in agent.programs.catalog.apps}
    table = {50: proc(r"C:\Program Files\WindowsApps\Solitaire\Solitaire.exe", pid=50,
                      pfn="Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe"),
             51: proc(r"C:\Users\kind\AppData\Local\Roblox\Roblox.exe", pid=51)}
    agent.programs.procs = Procs(table)
    pol = p.Policy(version="v", timezone="Europe/Berlin", users={"lisa": 1}, children=[{
        "id": 1, "name": "Lisa", "game_apps": [roblox],
        "rules": [{"weekday": d, "quota_minutes": 600, "window_start": "00:00", "window_end": "23:59",
                   "game_minutes": 1} for d in range(7)]}])
    agent.policy = pol
    assert agent.is_game(roblox, "lisa") and not agent.is_game(roblox) and agent.is_game(
        "appx:Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe") and not agent.is_game(
        "appx:Microsoft.GamingApp_8wekyb3d8bbwe")
    fake.sessions = [(2, api.WTSActive, "lisa", 1, 555)]

    async def main():
        await agent.tick_once()
        for _ in range(13):     # 65 s Spielen bei 1 min Spiele-Limit
            clock[0] += 5
            await agent.tick_once()
        today = agent.now_local().date()
        assert agent.playing["lisa"] == {"appx:Microsoft.MicrosoftSolitaireCollection_8wekyb3d8bbwe": "Solitaire",
                                         roblox: "Roblox"}
        assert agent.usage.used_game_seconds(1, ["lisa"], today) >= 60
        assert {k for _u, _d, k, _s in agent.apptime.report_entries(today)} >= {roblox}
        assert sorted(set(agent.programs.procs.killed)) == [50, 51]          # Spiele geschlossen
        assert agent.tray_state("lisa").gaming
        assert any(getattr(m, "title", "") == "Spielzeit aufgebraucht" for m in notes)
    asyncio.run(main())
