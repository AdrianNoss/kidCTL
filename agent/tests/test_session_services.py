"""Hintergrunddienste der Sitzung als Basis, ldd-Prüfmodus, Abstammung über fork()-Events."""

import asyncio
import os
import struct

from kidctl_agent import procs, programs
from kidctl_agent.programs import Allowed, classify
from kidctl_agent.procs import ProcInfo
from kidctl_agent.watcher import ProgramWatcher

STEAM = "/srv/steam/kind/"
LOGGER = STEAM + "ubuntu12_32/steam-runtime/usr/libexec/steam-runtime-tools-0/srt-logger"


def proc(exe, argv=None, pid=100, ppid=1, uid=1001, start=1):
    return ProcInfo(pid=pid, uid=uid, ppid=ppid, start=start, exe=exe, argv=tuple([exe] if argv is None else argv),
                    tty=False, flatpak_id=None)


def test_service_exes(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for n in ("daemon", "tray", "obexd", "dbusd", "hidden"):
        (bindir / n).write_text("")
        (bindir / n).chmod(0o755)
    auto, dbus, user = tmp_path / "autostart", tmp_path / "dbus", tmp_path / "user"
    for d in (auto, dbus, user):
        d.mkdir()
    (auto / "tray.desktop").write_text(f"[Desktop Entry]\nName=T\nExec=env A=1 {bindir}/tray --x %u\n"
                                       f"[Desktop Action y]\nExec={bindir}/daemon\n")
    (auto / "off.desktop").write_text(f"[Desktop Entry]\nExec={bindir}/hidden\nHidden=true\n")
    (dbus / "a.service").write_text(f"[D-BUS Service]\nName=org.x\nExec={bindir}/dbusd --session\n")
    (user / "obex.service").write_text(f"[Unit]\nDescription=o\n[Service]\nExecStart=-{bindir}/obexd\n")
    (user / "bad.service").write_text("[Service]\nExecStart=/nicht/da\n")
    (user / "open.service").write_text(f"[Service]\nExecStart={bindir}/daemon\n")
    (user / "open.service").chmod(0o666)                                       # für alle beschreibbar
    sources = ((str(auto), ".desktop", "Exec", "Desktop Entry"), (str(dbus), ".service", "Exec", "D-BUS Service"),
               (str(user), ".service", "ExecStart", "Service"), (str(tmp_path / "fehlt"), ".service", "ExecStart",
                                                                                "Service"))
    found = programs.service_exes(sources, owner=os.getuid())
    assert found == {str(bindir / "tray"), str(bindir / "dbusd"), str(bindir / "obexd")}
    assert programs.service_exes(sources, owner=0) == set() or os.getuid() == 0   # fremder Besitzer: nichts


def test_command_exe():
    assert programs._command_exe("@-/usr/bin/true arg") == os.path.realpath("/usr/bin/true")
    assert programs._command_exe("true") == os.path.realpath("/usr/bin/true")
    assert programs._command_exe("env A=1 B=2 true") == os.path.realpath("/usr/bin/true")
    assert programs._command_exe("env -u X A=1 true") is None
    assert programs._command_exe("'unbalanced") is None and programs._command_exe("") is None


def test_service_exes_filtered_like_base():
    # Apps, Shells und Ausschlüsse bleiben Eltern-Entscheidung
    files = ["/usr/bin/kdeconnectd", "/usr/bin/dolphin", "/usr/bin/bash", "/usr/bin/systemd-nspawn"]
    spec = programs.BaseSpec(excludes=programs.BUILTIN_EXCLUDE + ["/usr/bin/systemd-nspawn"])
    assert programs.base_paths(files, {"/usr/bin/dolphin"}, spec.excludes) == {"/usr/bin/kdeconnectd"}


def test_loader_only_for_ldd():
    a = Allowed(["/usr/bin/*"], set())
    ld = "/usr/lib/ld-linux-x86-64.so.2"
    assert classify(proc(ld, [ld, "--verify", "/usr/bin/x"]), a) is None
    assert classify(proc(ld, [ld, "--list", "/usr/bin/x"]), a) is None
    assert classify(proc(ld, [ld, "/usr/bin/x"]), a, environ=lambda: {"LD_TRACE_LOADED_OBJECTS": "1"}) is None
    v = classify(proc(ld, [ld, "/usr/lib/blocked/game"]), a, environ=lambda: {})
    assert v is not None and v.key == f"exe:{ld}"
    assert classify(proc(ld, [ld, "--preload", "x", "--list", "y"]), a, environ=lambda: {}) is not None


def test_netlink_fork_events():
    def event(what, *ints):
        payload = struct.pack("=IIQ", what, 0, 0) + struct.pack(f"={len(ints)}i", *ints)
        cn = struct.pack("=IIIIHH", 1, 1, 0, 0, len(payload), 0) + payload
        msg = struct.pack("=IHHII", 16 + len(cn), 3, 0, 0, 0) + cn
        return msg + b"\0" * ((-len(msg)) % 4)
    data = (event(procs.PROC_EVENT_FORK, 10, 10, 11, 11) + event(procs.PROC_EVENT_FORK, 10, 10, 12, 10)  # Thread
            + event(procs.PROC_EVENT_EXEC, 11, 11))
    assert procs.parse_events(data) == [(procs.PROC_EVENT_FORK, 10, 11), (procs.PROC_EVENT_EXEC, 11, 0)]
    assert procs.parse_exec_events(data) == [11]


def _watcher(ps):
    killed = []

    async def notify(*a, **kw):
        pass

    class FakeAgent:
        policy = None
        desktop = type("D", (), {"notify": staticmethod(notify)})()

    w = ProgramWatcher(FakeAgent(), kill=lambda pid, sig: killed.append(pid), read=lambda pid: ps.get(pid))
    w.base_exact = frozenset({"/usr/bin/kwin_wayland"})
    w._targets = {1001: ("kind", "enforce", Allowed(["/usr/bin/kwin_wayland"], {"exe:/usr/bin/steam"},
                                                     steam_root=STEAM))}
    return w, killed


def test_reparented_child_keeps_fork_lineage():
    """srt-logger: von einer Subshell von steam.sh gestartet; die Subshell endet, systemd --user wird
    Elternprozess. Über die fork()-Events bleibt die Abstammung von Steam erhalten."""
    systemd = proc("/usr/lib/systemd/systemd", ["/usr/lib/systemd/systemd", "--user"], pid=30)
    steam_sh = proc("/usr/bin/bash", ["/bin/bash", STEAM + "steam.sh"], pid=40, ppid=30, start=5)
    logger = proc(LOGGER, pid=42, ppid=30, start=7)
    ps = {30: systemd, 40: steam_sh, 42: logger}
    w, killed = _watcher(ps)
    asyncio.run(w.check_pid(42))
    assert killed == [42]                                   # ohne fork()-Wissen: gesperrt
    w, killed = _watcher(ps)
    w.on_fork(30, 40)
    w.on_fork(40, 41)                                       # Subshell (beendet)
    w.on_fork(41, 42)
    asyncio.run(w.check_pid(42))
    assert killed == []
    # wiederverwendete PID mit späterem Start gilt nicht als Vorfahr
    ps[40] = proc("/usr/bin/bash", ["/bin/bash", STEAM + "steam.sh"], pid=40, ppid=30, start=9)
    w, killed = _watcher(ps)
    for parent, child in ((30, 40), (40, 41), (41, 42)):
        w.on_fork(parent, child)
    asyncio.run(w.check_pid(42))
    assert killed == [42]


def test_fork_map_bounded(monkeypatch):
    from kidctl_agent import watcher
    monkeypatch.setattr(watcher, "MAX_FORKS", 3)
    w, _ = _watcher({})
    for child in range(10, 15):
        w.on_fork(1, child)
    w.on_fork(2, 12)                                        # PID neu vergeben: aktualisiert
    assert list(w.forks.items()) == [(13, 1), (14, 1), (12, 2)]


def test_packaged_base_excludes_containers():
    from pathlib import Path
    spec = programs.load_base(Path(__file__).resolve().parents[2] / "packaging/arch/base-programs.txt")
    for exe in ("/usr/bin/systemd-nspawn", "/usr/bin/systemd-vmspawn", "/usr/bin/socat1"):
        assert programs.base_paths([exe], set(), spec.excludes) == frozenset()
    assert "/usr/bin/which" in spec.globs
