"""Gespieltes Spiel: Erkennung (Steam/Heroic), Namen, Programmzeit je Spiel, „spielt gerade“."""

import getpass
import json
import os
from datetime import date

from kidctl_agent import agent as agent_mod
from kidctl_agent import kidmounts
from kidctl_agent.apptime import AppIndex, AppTime
from kidctl_agent.games import GameNames, game_id, read_small
from test_programs import proc

DAY = date(2026, 10, 19)


def test_game_id_from_environment():
    assert game_id({"SteamAppId": "620"}) == ("game:steam:620", "steam")
    assert game_id({"SteamAppId": "0"}) is None and game_id({"SteamAppId": "62 0"}) is None
    assert game_id({"HEROIC_APP_NAME": "1423049311", "HEROIC_APP_SOURCE": "gog", "SteamAppId": "0"}) == \
        ("game:heroic:gog:1423049311", "heroic")
    assert game_id({"HEROIC_APP_NAME": "../x", "HEROIC_APP_SOURCE": "gog"}) is None
    assert game_id({"HEROIC_APP_NAME": "abc", "HEROIC_APP_SOURCE": "boese"}) is None
    assert game_id({}) is None


def test_names_from_steam_manifest_and_heroic_library(tmp_path):
    lib = tmp_path / "steam"
    (lib / "steamapps").mkdir(parents=True)
    (lib / "steamapps/appmanifest_620.acf").write_text('"AppState"\n{\n\t"appid"\t\t"620"\n\t"name"\t\t"Portal 2"\n}\n')
    home = tmp_path / "home"
    (home / ".config/heroic/store_cache").mkdir(parents=True)
    (home / ".config/heroic/store_cache/gog_library.json").write_text(
        json.dumps({"games": [{"app_name": "1423049311", "title": "Cyberpunk 2077\x07"}]}))
    (home / ".config/heroic/legendaryConfig/legendary").mkdir(parents=True)
    (home / ".config/heroic/legendaryConfig/legendary/installed.json").write_text(
        json.dumps({"Fortnite": {"title": "Fortnite", "install_path": "/x"}}))
    n = GameNames()
    assert n.name("game:steam:620", str(home), str(lib)) == "Portal 2"
    assert n.name("game:steam:7", str(home), str(lib)) == "Steam-Spiel 7"
    assert n.name("game:heroic:gog:1423049311", str(home), str(lib)) == "Cyberpunk 2077"   # Steuerzeichen weg
    assert n.name("game:heroic:epic:Fortnite", str(home), str(lib)) == "Fortnite"
    assert n.name("game:heroic:gog:unbekannt", str(home), str(lib)) == "unbekannt"


def test_read_small_refuses_symlink_fifo_and_large(tmp_path):
    (tmp_path / "f").write_text("x" * 10)
    (tmp_path / "l").symlink_to(tmp_path / "f")
    os.mkfifo(tmp_path / "p")
    assert read_small(str(tmp_path / "f"), 100) == b"x" * 10
    assert read_small(str(tmp_path / "l"), 100) is None
    assert read_small(str(tmp_path / "p"), 100) is None       # hängt nicht
    assert read_small(str(tmp_path / "f"), 5) is None


def test_agent_counts_game_instead_of_steam_and_reports_playing(monkeypatch, tmp_path):
    me = getpass.getuser()
    monkeypatch.setattr(kidmounts, "STEAM_ROOT", agent_mod.STEAM_ROOT)  # im Betrieb identisch
    steam_lib = f"{agent_mod.STEAM_ROOT}/{me}"
    running = {1: proc(f"{steam_lib}/ubuntu12_32/steam", pid=1),                       # Steam-Client
               2: proc(f"{steam_lib}/steamapps/common/Portal 2/portal2_linux", pid=2),  # Spiel
               3: proc("/usr/lib/firefox/firefox", pid=3)}
    env = {2: {"SteamAppId": "620"}}
    reads = []
    monkeypatch.setattr(agent_mod, "pids_of", lambda uids: list(running))
    monkeypatch.setattr(agent_mod, "read_proc", lambda pid: running.get(pid))
    monkeypatch.setattr(agent_mod, "read_environ", lambda pid: reads.append(pid) or env.get(pid, {}))
    monkeypatch.setattr(agent_mod.GameNames, "name", lambda self, key, home, lib: "Portal 2")
    a = agent_mod.Agent(logind=None, desktop=None)
    a.app_index = AppIndex({"exe:/usr/bin/steam", "exe:/usr/lib/firefox/firefox"})
    a._count_apps({me}, DAY, 5.0)
    d = DAY.isoformat()
    assert a.apptime.local == {(me, d, "exe:/usr/bin/steam"): 5.0, (me, d, "game:steam:620"): 5.0,
                               (me, d, "exe:/usr/lib/firefox/firefox"): 5.0}
    assert a.playing == {me: {"game:steam:620": "Portal 2"}}
    assert sorted(reads) == [1, 2]                     # Umgebung nur bei Kandidaten (Spieleordner), Firefox nicht
    a._count_apps({me}, DAY, 5.0)
    assert sorted(reads) == [1, 2]                     # zwischengespeichert
    del running[2]
    a._count_apps({me}, DAY, 5.0)
    assert a.playing == {} and a.apptime.local[(me, d, "game:steam:620")] == 10.0
    a.apptime.save()
    assert AppTime.load().names == {"game:steam:620": "Portal 2"}
