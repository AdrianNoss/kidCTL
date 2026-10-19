"""Container-Prozesse (eigener Mount-Namespace): Entscheidung nach der echten Datei, nicht dem Pfad."""

import dataclasses
import os

import pytest

from kidctl_agent import programs
from kidctl_agent.programs import Allowed, classify
from test_heroic import ELECTRON, HEROIC, MAIN, ON, PKG, PLASMA
from test_programs import proc

# Systemdateien (Pfad -> Inode); im Container gesehene Dateien über /proc/<pid>/root/…
HOST = {"/usr/bin/kate": 1, "/usr/bin/python3.14": 2, "/usr/lib/kidctl/tray.py": 3, ELECTRON: 4,
        f"{PKG}/app.asar": 5}


@pytest.fixture
def fs(monkeypatch):
    container: dict[str, int] = {}

    def fake_stat(path):
        ino = container.get(path, HOST.get(path)) if path.startswith("/proc/") else HOST.get(path)
        if ino is None:
            raise FileNotFoundError(path)
        return os.stat_result((0o100755, ino, 1, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(programs, "_stat", fake_stat)
    return container


def boxed(p, exe_ino, **kw):
    return dataclasses.replace(p, mnt_foreign=True, exe_id=(1, exe_ino), **kw)


BASE = Allowed(["/usr/lib/kidctl/*"], {"exe:/usr/bin/kate"}, frozenset(), [])


def test_same_file_in_container_allowed(fs):
    assert classify(boxed(proc("/usr/bin/kate", pid=7), 1), BASE) is None


def test_different_file_under_system_path_blocked(fs):
    v = classify(boxed(proc("/usr/bin/kate", pid=7), 99), BASE, ancestors=lambda: [PLASMA])
    assert v is not None and "Container" in v.detail


def test_script_replaced_in_container_blocked(fs):
    py = proc("/usr/bin/python3.14", ["python3", "/usr/lib/kidctl/tray.py"], pid=8)
    fs["/proc/8/root/usr/lib/kidctl/tray.py"] = 3
    assert classify(boxed(py, 2), BASE, environ=lambda: {}) is None
    fs["/proc/8/root/usr/lib/kidctl/tray.py"] = 77          # anderes Skript unter dem Systempfad
    assert classify(boxed(py, 2), BASE, ancestors=lambda: [], environ=lambda: {}) is not None


def test_electron_app_replaced_in_container_blocked(fs):
    main = boxed(MAIN, 4)
    fs[f"/proc/{MAIN.pid}/root{PKG}/app.asar"] = 5
    assert classify(main, ON, ancestors=lambda: [PLASMA], environ=lambda: {}) is None
    fs[f"/proc/{MAIN.pid}/root{PKG}/app.asar"] = 66
    assert classify(main, ON, ancestors=lambda: [PLASMA], environ=lambda: {}) is not None
    # … und gilt dann auch nicht als Launcher für Nachfahren
    wine = proc("/srv/heroic/kind/tools/wine/bin/wine64", pid=80)
    assert classify(wine, ON, ancestors=lambda: [main, PLASMA]) is not None


def test_launcher_runtime_container_allowed(fs):
    """Laufzeit-Container von Heroic (umu/pressure-vessel) haben eigene Dateien unter /usr – erlaubt."""
    pv = boxed(proc("/usr/lib/pressure-vessel/from-host/libexec/steam-runtime-tools-0/pv-adverb", pid=81), 55)
    assert classify(pv, ON, ancestors=lambda: [MAIN, PLASMA]) is None
    assert classify(pv, Allowed(["/usr/lib/kidctl/*"], set(), frozenset(), [], pkg_dirs={HEROIC: (PKG,)}),
                    ancestors=lambda: [MAIN, PLASMA]) is not None
