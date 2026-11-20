"""Spiele unter Windows: Steam/Epic erkennen, Launcher-Regel, Programm- und Spielzeit, Spiele-Limit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
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


# ---------- Spielerkennung erweitert (2026-11-19) ----------

def test_steam_root_from_user_registry(monkeypatch):
    monkeypatch.setattr(games, "_steam_registry_paths", lambda: ["", "c:/games/steam"])
    assert games.steam_root(isdir=lambda p: p == r"c:\games\steam") == r"c:\games\steam"   # HKU: Schrägstriche
    monkeypatch.setattr(games, "_steam_registry_paths", lambda: [])
    std = games.STEAM_DEFAULT
    assert games.steam_root(isdir=lambda p: p == std + r"\steamapps") == std             # Standardordner
    assert games.steam_root(isdir=lambda p: False) is None


def test_signature_follows_manifest_dirs(steam_tree):
    mtimes = {p: 1 for p in steam_tree.watch}
    stat = lambda p: SimpleNamespace(st_mtime_ns=mtimes[p])   # noqa: E731
    assert LIB2 + r"\steamapps" in steam_tree.watch and EPIC in steam_tree.watch
    before = games.signature(steam_tree, steam=STEAM, stat=stat)
    assert games.signature(steam_tree, steam=STEAM, stat=stat) == before
    mtimes[LIB2 + r"\steamapps"] = 2                                                  # neues appmanifest
    assert games.signature(steam_tree, steam=STEAM, stat=stat) != before


def test_launch_tracker():
    t = games.LaunchTracker()
    t.update({1: (0, "explorer.exe"), 10: (1, "steam.exe"), 11: (10, "steamwebhelper.exe"),
              12: (11, "steamwebhelper.exe")})
    assert t.launched == {}                                                           # Launcher und Helfer zählen nicht
    # Spiel-Bootstrapper und Spiel in einem Durchgang, Kind vor Eltern in der Liste
    t.update({1: (0, "explorer.exe"), 10: (1, "steam.exe"), 21: (20, "Game.exe"), 20: (10, "Launcher.exe"),
              22: (21, "UnityCrashHandler64.exe"), 23: (1, "notepad.exe")})
    assert t.launched == {20: "steam.exe", 21: "steam.exe"}
    # Launcher und Bootstrapper beendet: Spiel bleibt markiert, neues Kind des Spiels erbt
    t.update({1: (0, "explorer.exe"), 21: (20, "Game.exe"), 24: (21, "GameHelper.exe")})
    assert t.launcher_of(21) == "steam.exe" and t.launcher_of(24) == "steam.exe" and t.launcher_of(20) is None
    # Elternteil weg, bevor das Kind je gesehen wurde: keine Markierung (kein Raten)
    t.update({1: (0, "explorer.exe"), 30: (29, "Other.exe")})
    assert t.launcher_of(30) is None
    # bereits gesehene, nicht markierte Prozesse werden später nicht nachträglich markiert
    t.update({1: (0, "explorer.exe"), 30: (29, "Other.exe"), 40: (1, "battle.net.exe"), 41: (40, "Wow.exe")})
    assert t.launcher_of(30) is None and t.launcher_of(41) == "battle.net.exe"
    assert "wgc.exe" in games.LAUNCHERS


def test_exe_name_fallback():
    assert games.exe_name(r"C:\Games\Cool Game\CoolGame.exe") == "CoolGame"   # ohne Versionsinfos (Linux-Test)
    assert games.exe_game_key(r"C:\Games\Cool Game\CoolGame.exe") == r"game:exe:c:\games\cool game\coolgame.exe"


def test_watcher_refreshes_games_on_change(tmp_path, monkeypatch):
    from kidctl_agent.win.watcher import GAMES_CHECK, WinProgramWatcher
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    clock = [100.0]
    w = WinProgramWatcher(SimpleNamespace(), clock=lambda: clock[0])
    scans, sig = [], ["a"]
    w.scan_games = lambda: scans.append(1) or games.GameIndex(games=[("c:\\g", "game:steam:1", "G")])
    w.games_signature = lambda idx: sig[0]

    async def main():
        assert await w.refresh_games(force=True) and len(scans) == 1
        assert not await w.refresh_games()                      # im Takt: nichts geändert
        clock[0] += GAMES_CHECK
        assert not await w.refresh_games()                      # geprüft, gleich
        sig[0] = "b"
        assert not await w.refresh_games()                      # Takt noch nicht um
        clock[0] += GAMES_CHECK
        assert await w.refresh_games() and len(scans) == 2      # Manifest-Ordner geändert
        w.games_stale = True
        assert await w.refresh_games() and len(scans) == 3      # unbekannter Prozess in Bibliothek: sofort
        assert not w.games_stale
    asyncio.run(main())


def test_winagent_counts_launched_game(tmp_path, monkeypatch):
    """Rückfall: von Battle.net gestartetes Spiel ohne Manifest zählt als Spiel; Spiel in einer Steam-Bibliothek
    ohne Manifest löst ein Neueinlesen aus."""
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
    agent.tray = SimpleNamespace(send_to_user=lambda u, m: asyncio.sleep(0, 1),
                                 push_states=lambda now: asyncio.sleep(0), tray_icons=lambda: {}, clients=[])
    agent.programs.catalog = catalog.Catalog(apps=[])
    agent.programs.games = games.scan(steam=STEAM, epic_dir=EPIC, listdir=_listdir, read=lambda p: FILES.get(p, ""))
    bnet = r"C:\Program Files (x86)\Battle.net\Battle.net.exe"
    wow = r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe"
    fresh = LIB2 + r"\steamapps\common\New Game\newgame.exe"
    svc = r"C:\Program Files (x86)\Battle.net\LocalSvc.exe"      # Dienst im Launcher-Ordner (wie EALocalHostSvc)
    table = {60: proc(bnet, pid=60), 61: proc(wow, pid=61, ppid=60), 62: proc(fresh, pid=62),
             63: proc(svc, pid=63, ppid=60)}
    agent.programs.procs = Procs(table)
    agent.programs.launches.update({60: (1, "Battle.net.exe"), 61: (60, "Wow.exe"), 62: (1, "newgame.exe"),
                                    63: (60, "LocalSvc.exe")})
    agent.policy = p.Policy(version="v", timezone="Europe/Berlin", users={"lisa": 1}, children=[{
        "id": 1, "name": "Lisa", "rules": [{"weekday": d, "quota_minutes": 600, "window_start": "00:00",
                                            "window_end": "23:59"} for d in range(7)]}])
    fake.sessions = [(2, api.WTSActive, "lisa", 1, 555)]

    async def main():
        await agent.tick_once()
        clock[0] += 5
        await agent.tick_once()
        key = games.exe_game_key(wow)
        assert agent.playing["lisa"] == {key: "Wow"}                    # Launcher und seine Dienste zählen nicht
        assert agent.programs.games_stale                              # newgame.exe: Manifest fehlt im Index
    asyncio.run(main())


# ---------- Echte Launcher-Daten (VM-Snapshot „launchers“, anonymisiert) ----------

REAL = Path(__file__).parent / "fixtures" / "win_launchers"
REAL_STEAM = r"C:\Program Files (x86)\Steam"


@pytest.fixture
def real_index():
    reg = json.loads((REAL / "registry.json").read_text(encoding="utf-8"))
    files = {
        REAL_STEAM + r"\steamapps\libraryfolders.vdf": (REAL / "steam_libraryfolders.vdf").read_text(encoding="utf-8"),
        REAL_STEAM + r"\steamapps\appmanifest_684410.acf": (REAL / "steam_appmanifest_684410.acf").read_text(),
        REAL_STEAM + r"\steamapps\appmanifest_228980.acf": (REAL / "steam_appmanifest_228980.acf").read_text(),
        EPIC + r"\2B97A001AF4C3FDACFCDC9E2599C1D59.item": (REAL / "epic_absolutedrift.item").read_text(),
    }

    def listdir(path):
        names = [k[len(path) + 1:] for k in files if k.startswith(path + "\\") and "\\" not in k[len(path) + 1:]]
        if not names:
            raise OSError(path)
        return names

    def registry(keys):
        return [(name, values) for key in keys for name, values in reg.get(key, {}).items()]

    ea_xml = r"C:\Program Files\EA Games\SimCity 2000 SE\__Installer\installerdata.xml".lower()
    return games.scan(steam=REAL_STEAM, epic_dir=EPIC, listdir=listdir, read=lambda p: files.get(p, ""),
                      registry=registry, read_bytes=lambda p: (REAL / "battlenet_product.db").read_bytes(),
                      isfile=lambda p: p.lower() == ea_xml)


def test_real_launcher_formats(real_index):
    idx = real_index
    assert idx.game_of(REAL_STEAM + r"\steamapps\common\Bridge Constructor Portal\BridgeConstructorPortal.exe") == \
        ("game:steam:684410", "Bridge Constructor Portal")
    assert idx.game_of(REAL_STEAM + r"\steamapps\common\Steamworks Shared\_CommonRedist\x.exe") is None   # Redist
    assert idx.game_of(r"C:\Program Files\Epic Games\AbsoluteDrift\AbsoluteDrift.exe") == \
        ("game:epic:19927295d6e3467887d4e830d8c85963", "Absolute Drift")
    assert idx.game_of(r"C:\Program Files\GOG Galaxy\Games\Bio Menace\DOSBOX\dosbox.exe") == \
        ("game:gog:1449569170", "Bio Menace")
    assert idx.game_of(r"C:\Program Files\EA Games\SimCity 2000 SE\Game\Game\DOSBOX\Dosbox.exe") == \
        ("game:ea:{59d2c751-f7be-4e9f-9c8c-1f16013802c7}", "SimCity 2000 Special Edition")
    assert idx.game_of(r"C:\Program Files (x86)\Hearthstone\Hearthstone.exe") == ("game:battlenet:hs_beta", "Hearthstone")
    # Launcher selbst sind keine Spiele
    for exe in (r"C:\Program Files (x86)\Battle.net\Battle.net.exe", r"C:\ProgramData\Battle.net\Agent\Agent.exe",
                r"C:\Program Files (x86)\Steam\steam.exe", r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\upc.exe"):
        assert idx.game_of(exe) is None, exe
    assert idx.launcher_dirs["battle.net.exe"] == [r"c:\program files (x86)\hearthstone"]
    assert idx.launcher_dirs["galaxyclient.exe"] == [r"c:\program files\gog galaxy\games\bio menace"]


def test_battlenet_db_robust():
    assert games.battlenet_games(b"") == []
    assert games.battlenet_games(b"\x0a\xff\xff") == []                          # abgeschnitten
    evil = bytes([0x0a, 9, 0x0a, 1, ord("x"), 0x1a, 4, 0x0a, 2]) + b"C:"         # Laufwerk allein: kein Spieleordner
    assert games.battlenet_games(evil) == []


def test_signature_follows_registry(real_index):
    stamps = {k: 1 for k in real_index.watch_reg}
    stat = lambda p: SimpleNamespace(st_mtime_ns=1)   # noqa: E731
    before = games.signature(real_index, steam=REAL_STEAM, stat=stat, regstamp=stamps.get)
    stamps[games.GOG_KEYS[0]] = 2                                                     # neues GOG-Spiel
    assert games.signature(real_index, steam=REAL_STEAM, stat=stat, regstamp=stamps.get) != before
    assert games.BNET_DB in real_index.watch


def test_launcher_dirs_for_released_launchers(real_index, tmp_path):
    from kidctl_agent.win.watcher import WinProgramWatcher
    w = WinProgramWatcher.__new__(WinProgramWatcher)
    w.games = real_index
    dirs = w.launcher_dirs({r"exe:C:\Program Files (x86)\Battle.net\Battle.net.exe"})
    assert dirs == (r"c:\program files (x86)\hearthstone",)
    assert w.launcher_dirs({"exe:C:\\x\\other.exe"}) == ()


def test_short_paths_resolve_to_game(real_index, monkeypatch):
    """EA startet ``activationui.exe`` über einen 8.3-Pfad – zählt zum Spiel, nicht als eigenes."""
    short = r"C:\PROGRA~1\EAGAME~1\SIMCIT~1\Game\Game\DOSBox\core\activationui.exe"
    long = r"C:\Program Files\EA Games\SimCity 2000 SE\Game\Game\DOSBox\core\activationui.exe"
    monkeypatch.setattr(games, "long_path", lambda p: long if p == short else p)
    assert real_index.game_of(short) == ("game:ea:{59d2c751-f7be-4e9f-9c8c-1f16013802c7}", "SimCity 2000 Special Edition")
    assert games.exe_game_key(short) == "game:exe:" + long.lower()
