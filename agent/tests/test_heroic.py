"""Heroic: Paket-Ordner, Electron, Spiele-Launcher-Regel, Spieleordner außerhalb des Homes."""

import asyncio
import getpass
import os
import subprocess

from kidctl_agent import kidmounts
from kidctl_agent.kidmounts import KidMounts
from kidctl_agent.programs import Allowed, classify, package_dirs
from test_kidmounts_steam import _real_link
from test_programs import proc

ME = getpass.getuser()

HEROIC = "/usr/bin/heroic"
PKG = "/usr/lib/heroic-games-launcher"
ELECTRON = "/usr/lib/electron43/electron"
QL = f"""heroic-games-launcher /usr/
heroic-games-launcher /usr/bin/
heroic-games-launcher {HEROIC}
heroic-games-launcher /usr/lib/
heroic-games-launcher {PKG}/
heroic-games-launcher {PKG}/app.asar
heroic-games-launcher {PKG}/app.asar.unpacked/
heroic-games-launcher {PKG}/app.asar.unpacked/build/
heroic-games-launcher {PKG}/app.asar.unpacked/build/bin/x64/linux/gogdl
heroic-games-launcher /usr/share/icons/
heroic-games-launcher /usr/share/icons/hicolor/
electron43 /usr/lib/
electron43 /usr/lib/electron43/
electron43 /usr/lib/electron43/electron
hicolor-icon-theme /usr/share/icons/
hicolor-icon-theme /usr/share/icons/hicolor/
"""


def test_package_dirs_only_exclusive_top_dirs():
    assert package_dirs(QL, {HEROIC}) == {HEROIC: (PKG,)}
    assert package_dirs(QL, {"/usr/bin/fehlt"}) == {}


def heroic_allowed(*keys):
    return Allowed(["/usr/lib/kidctl/*"], set(keys), frozenset({"/usr/bin/kwin_wayland"}), [],
                   steam_root="/srv/steam/kind/", pkg_dirs={HEROIC: (PKG,)})


ON = heroic_allowed(f"exe:{HEROIC}")
OFF = heroic_allowed()
MAIN = proc(ELECTRON, [ELECTRON, f"{PKG}/app.asar"], pid=50)
LAUNCH = proc("/usr/bin/sh", ["sh", HEROIC], pid=49)
PLASMA = proc("/usr/bin/plasmashell", pid=5)


def test_electron_main_allowed_via_package_dir():
    assert classify(MAIN, ON, ancestors=lambda: [PLASMA], environ=lambda: {}) is None
    assert classify(LAUNCH, ON) is None                                  # Starter-Skript aus /usr/bin
    v = classify(MAIN, OFF, ancestors=lambda: [PLASMA], environ=lambda: {})
    assert (v.key, v.name) == (f"exe:{PKG}/app.asar", "app.asar")
    # Hilfsprogramme aus dem Paket-Ordner (GOG-Downloader)
    assert classify(proc(f"{PKG}/app.asar.unpacked/build/bin/x64/linux/gogdl"), ON) is None
    # gogdl/legendary sind Python-Zipapps (#!/usr/bin/env python3)
    zipapp = proc("/usr/bin/python3.14", ["python3", f"{PKG}/app.asar.unpacked/build/bin/x64/linux/gogdl", "auth"])
    assert classify(zipapp, ON) is None and classify(zipapp, OFF) is not None


def test_electron_other_apps_and_injection_blocked():
    other = proc(ELECTRON, [ELECTRON, "/usr/lib/anderes/app.asar"], pid=60)
    assert classify(other, ON, ancestors=lambda: [PLASMA], environ=lambda: {}).key == "exe:/usr/lib/anderes/app.asar"
    bare = proc(ELECTRON, [ELECTRON], pid=61)
    assert classify(bare, ON, ancestors=lambda: [PLASMA]).key == f"exe:{ELECTRON}"
    for argv in ([ELECTRON, "-r", "/home/kind/x.js", f"{PKG}/app.asar"],
                 [ELECTRON, "--inspect=9229", f"{PKG}/app.asar"],
                 [ELECTRON, "--remote-debugging-port=9222", f"{PKG}/app.asar"]):
        v = classify(proc(ELECTRON, argv, pid=62), ON, ancestors=lambda: [PLASMA], environ=lambda: {})
        assert v is not None, argv
    for env in ({"ELECTRON_RUN_AS_NODE": "1"}, {"NODE_OPTIONS": "--require /home/kind/x.js"}):
        v = classify(MAIN, ON, ancestors=lambda: [PLASMA], environ=lambda e=env: e)
        assert v is not None and "fremdem Code" in v.detail


def test_electron_helpers_need_allowed_main():
    renderer = proc(ELECTRON, [ELECTRON, "--type=renderer", "--foo"], pid=70)
    crashpad = proc("/usr/lib/electron43/chrome_crashpad_handler", pid=71)
    other_main = proc(ELECTRON, [ELECTRON, "/usr/lib/anderes/app.asar"], pid=72)
    for helper in (renderer, crashpad):
        assert classify(helper, ON, ancestors=lambda: [MAIN, PLASMA]) is None
        assert classify(helper, ON, ancestors=lambda: [other_main, PLASMA]) is not None
        assert classify(helper, ON, ancestors=lambda: [PLASMA]) is not None


def test_launcher_descendants_allowed_only_when_launcher_allowed():
    wine = proc("/srv/heroic/kind/tools/wine/GE-Proton/bin/wine64", pid=80)
    game = proc("/srv/heroic/kind/Games/Spiel/start.sh", pid=81)
    gogdl = proc(f"{PKG}/app.asar.unpacked/build/bin/x64/linux/gogdl", pid=79)
    assert classify(wine, ON, ancestors=lambda: [gogdl, MAIN, PLASMA]) is None
    assert classify(game, ON, ancestors=lambda: [MAIN, PLASMA]) is None
    assert classify(wine, OFF, ancestors=lambda: [gogdl, MAIN, PLASMA]) is not None
    assert classify(wine, ON, ancestors=lambda: [PLASMA]) is not None              # direkt gestartet
    # Proton aus der Steam-Bibliothek: nur mit Steam-Freigabe, dann auch über Heroic
    proton = proc("/srv/steam/kind/steamapps/common/Proton 9.0/files/bin/wine64", pid=82)
    both = heroic_allowed(f"exe:{HEROIC}", "exe:/usr/bin/steam")
    assert classify(proton, both, ancestors=lambda: [MAIN, PLASMA]) is None
    assert classify(proton, ON, ancestors=lambda: [MAIN, PLASMA]).name == "Steam"


def test_orphaned_launcher_child_keeps_lineage(tmp_path):
    """wineserver wird nach dem Start verwaist; nach einer Policy-Änderung (erneute Prüfung) darf er
    nicht beendet werden – solange Heroic freigegeben bleibt."""
    from kidctl_agent.watcher import ProgramWatcher

    wineserver = proc("/srv/heroic/kind/tools/wine/bin/wineserver", pid=90, uid=1001)
    procs = {50: MAIN, 90: wineserver}
    wineserver_child = wineserver.__class__(**{**wineserver.__dict__, "ppid": 50})
    killed = []

    async def notify(*a):
        pass

    class FakeAgent:
        policy = None
        desktop = type("D", (), {"notify": staticmethod(notify)})()

    w = ProgramWatcher(FakeAgent(), kill=lambda pid, sig: killed.append(pid), read=lambda pid: procs.get(pid))
    w.base_exact = frozenset({"/usr/bin/kwin_wayland"})
    allowed = ON
    w._targets = {1001: ("kind", "enforce", allowed)}
    procs[90] = wineserver_child                     # Start: Elternprozess ist Heroic
    asyncio.run(w.check_pid(90))
    assert killed == [] and (90, 1) in w.lineage
    procs[90] = wineserver                           # verwaist (ppid 1)
    w.handled.clear()                                # Policy geändert
    asyncio.run(w.check_pid(90))
    assert killed == []
    # Heroic-Freigabe entzogen -> beenden
    w._targets = {1001: ("kind", "enforce", OFF)}
    w.handled.clear()
    asyncio.run(w.check_pid(90))
    assert killed == [90]


# ---------- Spieleordner ----------

def _heroic_home(tmp_path, monkeypatch, existing=True):
    home = tmp_path / "home" / ME
    home.mkdir(parents=True)
    if existing:
        (home / "Games/Heroic/Spiel").mkdir(parents=True)
        (home / ".config/heroic/tools/wine").mkdir(parents=True)
    root = tmp_path / "srv-heroic"
    monkeypatch.setattr(kidmounts, "HEROIC_INSTALLED", tmp_path)
    monkeypatch.setattr(kidmounts, "HEROIC_ROOT", root)
    monkeypatch.setattr(kidmounts, "STEAM_ROOT_OWNER", os.getuid())
    monkeypatch.setattr(kidmounts, "safe_home", lambda u: str(home) if u == ME else None)
    return home, root / ME


def test_heroic_links_created(tmp_path, monkeypatch):
    home, lib = _heroic_home(tmp_path, monkeypatch, existing=False)
    m = KidMounts(run=_real_link, in_use=lambda h: True)
    m.ensure_libraries({ME})
    assert os.readlink(home / "Games/Heroic") == str(lib / "Games")
    assert os.readlink(home / ".config/heroic/tools") == str(lib / "tools")
    assert os.readlink(home / ".local/share/umu") == str(lib / "umu")
    assert (lib / "Games").is_dir() and (lib / "tools").is_dir()
    assert oct(lib.stat().st_mode & 0o777) == "0o700" and m.lib_problems == {}


def test_heroic_dirs_moved_after_logout(tmp_path, monkeypatch):
    home, lib = _heroic_home(tmp_path, monkeypatch)
    logged_in = {"yes": True}
    m = KidMounts(run=_real_link, in_use=lambda h: logged_in["yes"])
    m.ensure_libraries({ME})
    assert (home / "Games/Heroic").is_dir() and not (home / "Games/Heroic").is_symlink()
    assert m.lib_pending == {(ME, "Games/Heroic"), (ME, ".config/heroic/tools")}
    assert "nach der Abmeldung" in m.lib_problems[("heroic", ME)]
    logged_in["yes"] = False
    assert m.releasable()
    m.ensure_libraries({ME})
    assert os.readlink(home / "Games/Heroic") == str(lib / "Games")
    assert (lib / "Games/Spiel").is_dir() and (lib / "tools/wine").is_dir()
    assert m.lib_problems == {} and m.lib_pending == set()


def test_heroic_move_refuses_symlinked_components(tmp_path, monkeypatch):
    home, lib = _heroic_home(tmp_path, monkeypatch)
    victim = tmp_path / "fremd"
    (victim / "heroic/tools").mkdir(parents=True)
    import shutil
    shutil.rmtree(home / ".config")
    (home / ".config").symlink_to(victim)
    m = KidMounts(run=lambda *c: subprocess.CompletedProcess(c, 3, "", ""), in_use=lambda h: False)
    m.ensure_libraries({ME})
    assert (victim / "heroic/tools").is_dir()
    assert "nicht verschiebbar" in m.lib_problems[("heroic", ME)]


def test_heroic_time_attributed_to_launcher():
    from kidctl_agent.apptime import AppIndex
    ix = AppIndex({f"exe:{HEROIC}"}, {HEROIC: (PKG,)})
    assert ix.key_for(MAIN) == f"exe:{HEROIC}"
    assert ix.key_for(proc(f"{PKG}/app.asar.unpacked/build/bin/x64/linux/gogdl")) == f"exe:{HEROIC}"
    assert ix.key_for(proc(ELECTRON, [ELECTRON, "/usr/lib/anderes/app.asar"])) is None


def test_electron_rewritten_cmdline():
    """Chromium überschreibt /proc/<pid>/cmdline mit einem einzigen String (auf dem
    Entwicklungsrechner mit electron43 nachgemessen) – Hauptprozess und Hilfsprozesse."""
    main = proc(ELECTRON, [f"{ELECTRON} {PKG}/app.asar"], pid=50)
    zygote = proc(ELECTRON, [f"{ELECTRON} --type=zygote --no-zygote-sandbox"], pid=51)
    assert classify(main, ON, ancestors=lambda: [PLASMA], environ=lambda: {}) is None
    assert classify(zygote, ON, ancestors=lambda: [main, PLASMA]) is None
    assert classify(zygote, ON, ancestors=lambda: [PLASMA]) is not None
    bad = proc(ELECTRON, [f"{ELECTRON} --inspect=9229 {PKG}/app.asar"], pid=52)
    assert classify(bad, ON, ancestors=lambda: [PLASMA], environ=lambda: {}) is not None
    from kidctl_agent.apptime import AppIndex
    assert AppIndex({f"exe:{HEROIC}"}, {HEROIC: (PKG,)}).key_for(main) == f"exe:{HEROIC}"
