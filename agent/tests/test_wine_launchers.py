"""Spiele aus Lutris und Bottles (Wine): Erkennung anhand echter Daten aus der Test-VM (fixtures/wine_launchers)."""

import getpass
import os
import shutil
from datetime import date
from pathlib import Path

from kidctl_agent import agent as agent_mod
from kidctl_agent.apptime import AppIndex
from kidctl_agent.games import GameNames, bottles_program, bottles_programs, game_id, read_small
from test_programs import proc

FIX = Path(__file__).parent / "fixtures" / "wine_launchers"
DAY = date(2026, 11, 23)


def environ(name: str) -> dict[str, str]:
    out = {}
    for line in (FIX / name).read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k] = v
    return out


def bottle(tmp_path) -> Path:
    """Bottle-Ordner wie in der VM (WINEPREFIX) mit echter bottle.yml."""
    b = tmp_path / "Testflasche"
    b.mkdir()
    shutil.copy(FIX / "bottles_bottle.yml", b / "bottle.yml")
    return b


def test_lutris_game_from_real_environment():
    env = environ("lutris_environ.txt")
    assert game_id(env) == ("game:lutris:Test-Spiel: Wüste", "lutris")
    assert GameNames().name("game:lutris:Test-Spiel: Wüste", "/home/kind", "/x") == "Test-Spiel: Wüste"


def test_lutris_needs_uuid_and_name():
    env = environ("lutris_environ.txt")
    assert game_id({k: v for k, v in env.items() if k != "LUTRIS_GAME_UUID"}) is None   # GAME_NAME allein: kein Lutris
    assert game_id({**env, "LUTRIS_GAME_UUID": "kein-uuid"}) is None
    assert game_id({**env, "GAME_NAME": " \x07 "}) is None
    assert game_id({**env, "GAME_NAME": "Spiel\x1b[31m" + "x" * 300})[0] == "game:lutris:Spiel[31m" + "x" * 91


def test_bottles_programs_parsed_from_real_yaml(tmp_path):
    progs = bottles_programs(str(bottle(tmp_path) / "bottle.yml"), os.getuid())
    assert progs == [
        ("06cf83f9-e633-4637-af6f-3db1147ed2a1", "Wüsten-Rennen",
         "/home/kind/.var/app/com.usebottles.bottles/data/bottles/bottles/Testflasche/drive_c/Spiele/Wüsten Rennen/Rennen.exe"),
        ("cbf630c0-1a15-40ce-b39d-e864069c4836", "Editor",
         "/home/kind/.var/app/com.usebottles.bottles/data/bottles/bottles/Testflasche/drive_c/Tools/Editor.exe")]


def test_bottles_program_matched_by_windows_path(tmp_path):
    env = {**environ("bottles_environ.txt"), "WINEPREFIX": str(bottle(tmp_path))}
    argv = ("C:\\Spiele\\Wüsten Rennen\\Rennen.exe",) + ("",) * 5
    assert bottles_program(env, argv, os.getuid()) == \
        ("game:bottles:Testflasche:06cf83f9-e633-4637-af6f-3db1147ed2a1", "Wüsten-Rennen")
    assert bottles_program(env, ("c:\\tools\\EDITOR.EXE",), os.getuid())[1] == "Editor"     # Windows: Groß/klein egal
    # Wine-Hilfsprozesse und nicht eingetragene Programme zählen nicht
    for helper in ("C:\\windows\\system32\\services.exe", "C:\\windows\\system32\\explorer.exe", "start.exe", ""):
        assert bottles_program(env, (helper,), os.getuid()) is None
    assert bottles_program(env, (), os.getuid()) is None
    assert bottles_program({**env, "BOTTLE": ""}, argv, os.getuid()) is None                 # nicht aus Bottles


def test_bottles_program_matched_by_unix_path_and_basename(tmp_path):
    env = {**environ("bottles_environ.txt"), "WINEPREFIX": str(bottle(tmp_path))}
    unix = "/home/kind/.var/app/com.usebottles.bottles/data/bottles/bottles/Testflasche/drive_c/Tools/Editor.exe"
    assert bottles_program(env, (unix,), os.getuid())[1] == "Editor"
    assert bottles_program(env, ("D:\\Kopie\\Rennen.exe",), os.getuid())[1] == "Wüsten-Rennen"  # anderes Laufwerk


def test_bottles_yaml_only_regular_file_of_child(tmp_path):
    b = bottle(tmp_path)
    assert bottles_programs(str(b / "bottle.yml"), os.getuid() + 1) == []      # gehört nicht dem Kind
    assert read_small(str(b / "bottle.yml"), 1 << 20, uid=os.getuid() + 1) is None
    (tmp_path / "link").mkdir()
    (tmp_path / "link" / "bottle.yml").symlink_to(b / "bottle.yml")
    assert bottles_programs(str(tmp_path / "link" / "bottle.yml"), os.getuid()) == []
    env = {"BOTTLE": "x", "WINEPREFIX": "relativ"}
    assert bottles_program(env, ("C:\\Spiele\\Wüsten Rennen\\Rennen.exe",), os.getuid()) is None


def test_bottles_yaml_without_programs(tmp_path):
    (tmp_path / "bottle.yml").write_text("Arch: win64\nExternal_Programs: {}\nName: Leer\n")
    assert bottles_programs(str(tmp_path / "bottle.yml"), os.getuid()) == []


def test_agent_counts_lutris_and_bottles_games(monkeypatch, tmp_path):
    me = getpass.getuser()
    b = bottle(tmp_path)
    running = {
        1: proc("/usr/lib/wine/x86_64-unix/wine-preloader", pid=1,
                argv=["/home/kind/Games/test-spiel/drive_c/windows/notepad.exe"] + [""] * 3),
        2: proc(f"{tmp_path}/runners/soda-11.0-10/lib/wine/x86_64-unix/wine-preloader", pid=2, uid=os.getuid(),
                argv=["C:\\Spiele\\Wüsten Rennen\\Rennen.exe"]),
        3: proc(f"{tmp_path}/runners/soda-11.0-10/lib/wine/x86_64-unix/wine-preloader", pid=3, uid=os.getuid(),
                argv=["C:\\windows\\system32\\services.exe"]),
        4: proc("/usr/bin/python3.14", pid=4, argv=["python3", "/usr/share/lutris/bin/lutris-wrapper"]),
    }
    env = {1: environ("lutris_environ.txt"), 4: environ("lutris_environ.txt"),
           2: {**environ("bottles_environ.txt"), "WINEPREFIX": str(b)},
           3: {**environ("bottles_environ.txt"), "WINEPREFIX": str(b)}}
    monkeypatch.setattr(agent_mod, "pids_of", lambda uids: list(running))
    monkeypatch.setattr(agent_mod, "read_proc", lambda pid: running.get(pid))
    monkeypatch.setattr(agent_mod, "read_environ", lambda pid: env.get(pid, {}))
    a = agent_mod.Agent(logind=None, desktop=None)
    a.app_index = AppIndex(set())
    a._count_apps({me}, DAY, 5.0)
    lutris, bottles = "game:lutris:Test-Spiel: Wüste", "game:bottles:Testflasche:06cf83f9-e633-4637-af6f-3db1147ed2a1"
    assert a.playing == {me: {lutris: "Test-Spiel: Wüste", bottles: "Wüsten-Rennen"}}
    d = DAY.isoformat()
    assert a.apptime.local[(me, d, lutris)] == 5.0 and a.apptime.local[(me, d, bottles)] == 5.0
    assert me in a.gaming
