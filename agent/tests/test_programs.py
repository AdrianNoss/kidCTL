"""Programm-Whitelist im Agenten: Entscheidung, .desktop-Katalog, Watcher, Netlink-Parser."""

import asyncio
import signal
import struct
from datetime import time
from pathlib import Path

from kidctl_common import protocol as p

from kidctl_agent import apps, procs, programs, store
from kidctl_agent.programs import Allowed, classify, script_arg
from kidctl_agent.procs import ProcInfo
from kidctl_agent.watcher import ProgramWatcher

BASE = ["/usr/bin/plasmashell", "/usr/lib/kf6/*", "/usr/bin/xdg-open", "/usr/lib/kidctl/*"]


def proc(exe, argv=None, tty=False, flatpak=None, pid=100, uid=1001, start=1):
    return ProcInfo(pid=pid, uid=uid, ppid=1, start=start, exe=exe, argv=tuple([exe] if argv is None else argv), tty=tty,
                    flatpak_id=flatpak)


def allowed(*keys):
    return Allowed(BASE, set(keys))


def test_script_arg():
    assert script_arg("python3", ("python3", "-E", "-s", "/usr/lib/kidctl/tray.py")) == "/usr/lib/kidctl/tray.py"
    assert script_arg("python3", ("python3", "-c", "print(1)")) is None
    assert script_arg("python3", ("python3", "-Ic", "x")) is None
    assert script_arg("python3", ("python3", "-m", "http.server")) is None
    assert script_arg("python3", ("python3",)) is None
    assert script_arg("python3", ("python3", "-W", "ignore", "/x.py")) == "/x.py"
    assert script_arg("perl", ("perl", "-e", "print 1")) is None
    assert script_arg("node", ("node", "--eval=1")) is None
    assert script_arg("lua5.4", ("lua5.4", "/usr/share/x.lua")) == "/usr/share/x.lua"


def test_classify_basics():
    a = allowed("exe:/usr/lib/firefox/firefox")
    assert classify(proc("/usr/bin/plasmashell"), a) is None                   # Basis
    assert classify(proc("/usr/lib/kf6/kioworker"), a) is None                 # Basis-Glob
    assert classify(proc("/usr/lib/firefox/firefox"), a) is None               # Eltern-Freigabe
    assert classify(proc("/usr/lib/firefox/pingsender"), a) is None            # App-Verzeichnis
    v = classify(proc("/usr/bin/konsole"), a)
    assert v.key == "exe:/usr/bin/konsole" and v.name == "konsole"
    # /usr/bin ist generisch -> keine App-Verzeichnis-Regel
    b = allowed("exe:/usr/bin/supertuxkart")
    assert classify(proc("/usr/bin/supertuxkart"), b) is None
    assert classify(proc("/usr/bin/konsole"), b) is not None


def test_flatpak():
    a = allowed("flatpak:org.tuxpaint.Tuxpaint")
    assert classify(proc("/app/bin/tuxpaint", flatpak="org.tuxpaint.Tuxpaint"), a) is None
    v = classify(proc("/app/bin/minetest", flatpak="net.minetest.Minetest"), a)
    assert v.key == "flatpak:net.minetest.Minetest"


def test_shells_and_interpreters():
    a = allowed()
    assert classify(proc("/usr/bin/bash", ["sh", "-c", "foo"]), a) is None       # Befehl
    v = classify(proc("/usr/bin/bash", ["bash"], tty=True), a)
    assert v.detail == "interaktive Shell"
    assert classify(proc("/usr/bin/bash", ["-bash"], tty=True), a).detail == "interaktive Shell"  # TTY-Login
    assert classify(proc("/usr/bin/bash", ["bash", "-i"]), a).detail == "interaktive Shell"
    assert classify(proc("/usr/bin/bash", ["bash", "-s"]), a).detail == "interaktive Shell"
    # Regression: SDDM startet die Plasma-Sitzung so – mit Steuerterminal (tty2)
    sddm = ["/bin/bash", "--login", "/usr/share/sddm/scripts/wayland-session",
            "/usr/lib/plasma-dbus-run-session-if-needed", "/usr/bin/startplasma-wayland"]
    assert classify(proc("/usr/bin/bash", sddm, tty=True), a) is None
    assert classify(proc("/usr/bin/bash", ["/bin/sh", "/usr/bin/xdg-open", "x"]), a) is None
    v = classify(proc("/usr/bin/bash", ["bash", "-o", "posix", "/home/kind/x.sh"]), a)
    assert v.key == "exe:/home/kind/x.sh" and v.detail == "Skript (bash)"
    assert classify(proc("/usr/bin/python3.14", ["python3", "-E", "-s", "/usr/lib/kidctl/kidctl-tray.py"]), a) is None
    v = classify(proc("/usr/bin/python3.14", ["python3", "-c", "import os"]), a)
    assert v.key == "exe:/usr/bin/python3.14" and "Interpreter" in v.detail
    v = classify(proc("/usr/bin/python3.14", ["python3", "/home/kind/spiel.py"]), a)
    assert v.key == "exe:/home/kind/spiel.py" and v.name == "spiel.py"
    # Eltern geben ein Python-Skript-Programm frei
    s = allowed("exe:/usr/bin/hp-toolbox")
    assert classify(proc("/usr/bin/python3.14", ["python3", "/usr/bin/hp-toolbox"]), s) is None
    # Eltern geben Python ausdrücklich frei -> alles erlaubt
    assert classify(proc("/usr/bin/python3.14", ["python3", "-c", "x"]), allowed("exe:/usr/bin/python3.14")) is None


def test_desktop_parsing(tmp_path):
    d = tmp_path / "applications"
    d.mkdir()
    (d / "bash.desktop").write_text("[Desktop Entry]\nType=Application\nName=Shell\nName[de]=Kommandozeile\nExec=bash %U\nIcon=utilities-terminal\n")
    (d / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hidden\nExec=bash\nNoDisplay=true\n")
    (d / "flat.desktop").write_text("[Desktop Entry]\nType=Application\nName=Tux Paint\nExec=/usr/bin/flatpak run --branch=stable org.tuxpaint.Tuxpaint\nX-Flatpak=org.tuxpaint.Tuxpaint\n")
    (d / "env.desktop").write_text("[Desktop Entry]\nType=Application\nName=EnvApp\nExec=env FOO=1 -u BAR bash --x\n")
    (d / "gone.desktop").write_text("[Desktop Entry]\nType=Application\nName=Gone\nExec=/nicht/da\n")
    (d / "link.desktop").write_text("[Desktop Entry]\nType=Link\nName=L\nURL=x\n")
    found = {a.name: a for a in apps.scan_apps([str(d)], etc_shells="/nonexistent")}
    bash = str(Path(apps.resolve_exec("bash")))
    assert set(found) == {"Kommandozeile", "Tux Paint"}  # EnvApp == bash -> gleicher Schlüssel
    assert found["Kommandozeile"].key == f"exe:{bash}" and found["Kommandozeile"].desktop_id == "bash.desktop"
    assert found["Tux Paint"].key == "flatpak:org.tuxpaint.Tuxpaint"


def test_netlink_parser():
    def event(what, pid, tgid):
        payload = struct.pack("=IIQ", what, 0, 0) + struct.pack("=ii", pid, tgid)
        cn = struct.pack("=IIIIHH", 1, 1, 0, 0, len(payload), 0) + payload
        msg = struct.pack("=IHHII", 16 + len(cn), 3, 0, 0, 0) + cn
        return msg + b"\0" * ((-len(msg)) % 4)
    data = event(procs.PROC_EVENT_EXEC, 4242, 4240) + event(0x1, 1, 1) + event(procs.PROC_EVENT_EXEC, 7, 7)
    assert procs.parse_exec_events(data) == [4240, 7]


# ---------- Watcher ----------

class FakeDesktop:
    def __init__(self):
        self.sent = []

    async def notify(self, user, uid, title, body, critical=False, on_action=None, action=None):
        self.sent.append((user, title, body))


class FakeAgent:


    lang_of = staticmethod(lambda user: "de")
    def __init__(self, mode, allowed_programs=(), users=None):
        import getpass
        self.user = getpass.getuser()
        import pwd
        self.uid = pwd.getpwnam(self.user).pw_uid
        self.policy = p.Policy(version="v", timezone="Europe/Berlin", users=users or {self.user: 1}, children=[
            p.ChildPolicy(id=1, name="Max", program_mode=mode, allowed_programs=list(allowed_programs),
                          rules=[p.DayRule(weekday=d, quota_minutes=60, window_start=time(0),
                                           window_end=time(23, 59)) for d in range(7)])])
        self.desktop = FakeDesktop()

    def now_local(self):
        from datetime import datetime
        return datetime.now()

    def decisions(self, now):
        return {u: None for u in self.policy.users}


class FakeConn:
    connected = True

    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
        return True


def make_watcher(agent, procs_by_pid):
    killed = []
    clock = [0.0]
    w = ProgramWatcher(agent, kill=lambda pid, sig: killed.append((pid, sig)),
                       read=lambda pid: procs_by_pid.get(pid), list_pids=lambda uids: list(procs_by_pid),
                       clock=lambda: clock[0])
    w.spec = programs.BaseSpec(globs=list(BASE))
    w.base_exact = frozenset({"/usr/bin/kwin_wayland"})  # „berechnete“ Basisliste
    w.names = {"exe:/usr/bin/konsole": "Konsole"}
    return w, killed, clock


def test_watcher_enforce_kills_notifies_records():
    a = FakeAgent("enforce", ["exe:/usr/lib/firefox/firefox"])
    ps = {1: proc("/usr/bin/plasmashell", pid=1, uid=a.uid), 2: proc("/usr/bin/konsole", pid=2, uid=a.uid),
          3: proc("/usr/lib/firefox/firefox", pid=3, uid=a.uid), 4: proc("/usr/bin/konsole", pid=4, uid=0)}
    w, killed, clock = make_watcher(a, ps)
    asyncio.run(w.scan())
    assert killed == [(2, signal.SIGKILL)]                       # root-Prozess (uid 0) unangetastet
    assert a.desktop.sent == [(a.user, "Programm gesperrt",
                               "„Konsole“ ist nicht freigegeben. Du kannst deine Eltern um Freigabe bitten.")]
    assert [(i.key, i.name, i.access_kind) for i in w.recent_items(a.user)] == [("exe:/usr/bin/konsole", "Konsole", "program")]
    assert [(e.key, e.name, e.audit) for e in w.pending] == [("exe:/usr/bin/konsole", "Konsole", False)]
    # Erneuter Start desselben Programms kurz danach: wieder beendet, aber keine Meldungsflut
    ps[5] = proc("/usr/bin/konsole", pid=5, uid=a.uid, start=9)
    clock[0] += 10
    asyncio.run(w.scan())
    assert killed[-1] == (5, signal.SIGKILL) and len(a.desktop.sent) == 1 and len(w.pending) == 1
    # Protokoll übersteht Neustart; flush sendet und leert
    assert len(store.load("blocked.json")["events"]) == 1
    conn = FakeConn()
    asyncio.run(w.flush(conn))
    assert isinstance(conn.sent[0], p.BlockedReport) and w.pending == [] and store.load("blocked.json")["events"] == []


def test_watcher_audit_only_logs_once():
    a = FakeAgent("audit")
    ps = {2: proc("/usr/bin/konsole", pid=2, uid=a.uid)}
    w, killed, clock = make_watcher(a, ps)
    for _ in range(3):
        asyncio.run(w.scan())
    assert killed == [] and a.desktop.sent == []
    assert [(e.name, e.audit) for e in w.pending] == [("Konsole", True)]


def test_watcher_off_and_unmapped():
    a = FakeAgent("off")
    w, killed, _ = make_watcher(a, {2: proc("/usr/bin/konsole", pid=2, uid=a.uid)})
    asyncio.run(w.scan())
    b = FakeAgent("enforce", users={"jemand-anderes": 1})
    w2, killed2, _ = make_watcher(b, {2: proc("/usr/bin/konsole", pid=2, uid=b.uid)})
    asyncio.run(w2.scan())
    assert killed == killed2 == []


def test_policy_change_rechecks_running_processes():
    a = FakeAgent("enforce", ["exe:/usr/bin/konsole"])
    ps = {2: proc("/usr/bin/konsole", pid=2, uid=a.uid)}
    w, killed, _ = make_watcher(a, ps)
    asyncio.run(w.scan())
    assert killed == []
    a.policy.children[0].allowed_programs = []   # Freigabe entzogen
    w.policy_changed()
    asyncio.run(w.scan())
    assert killed == [(2, signal.SIGKILL)]


def test_base_file_loading(tmp_path):
    f = tmp_path / "base.txt"
    f.write_text("# Kommentar\npkg:plasma-desktop\n/usr/bin/plasmashell\n\n!/usr/bin/konsole\n")
    spec = programs.load_base(f)
    assert spec.packages == ["plasma-desktop"]
    assert spec.globs == programs.BUILTIN_BASE + ["/usr/bin/plasmashell"]
    assert spec.excludes == programs.BUILTIN_EXCLUDE + ["/usr/bin/konsole"]
    assert programs.load_base(tmp_path / "fehlt.txt") == programs.BaseSpec()


QI = """Name            : plasma-desktop
Version         : 6.7
Depends On      : plasma-workspace  libfoo>=1.0
Provides        : None

Name            : plasma-workspace
Depends On      : sh  kwin
Provides        : None

Name            : kwin-git
Depends On      : None
Provides        : kwin=6.7

Name            : bash
Depends On      : readline
                  glibc
Provides        : sh

Name            : firefox
Depends On      : None
Provides        : None
"""


def test_closure_and_base_paths():
    deps, provides = programs.parse_pacman_qi(QI)
    assert deps["bash"] == ["readline", "glibc"] and provides["kwin"] == "kwin-git"
    assert programs.closure(deps, provides, ["plasma-desktop", "fehlt"]) == {
        "plasma-desktop", "plasma-workspace", "kwin-git", "bash"}
    files = ["/usr/bin/plasmashell", "/usr/bin/bash", "/usr/bin/python3.14", "/usr/lib/libx.so.1",
             "/usr/lib/kf6/kioworker", "/usr/bin/systemsettings", "/usr/share/doc/x", "/usr/lib/ld-linux-x86-64.so.2",
             "/usr/bin/konsole"]
    base = programs.base_paths(files, app_exes={"/usr/bin/systemsettings"},
                               excludes=programs.BUILTIN_EXCLUDE + ["/usr/bin/konsole"])
    assert base == {"/usr/bin/plasmashell", "/usr/lib/kf6/kioworker"}


def test_excludes_beat_base_but_not_parents():
    a = Allowed(["/usr/bin/*"], {"exe:/usr/bin/konsole"}, frozenset(), ["/usr/bin/konsole", "/usr/bin/yakuake"])
    assert a.path_ok("/usr/bin/konsole")        # Eltern-Freigabe gewinnt
    assert not a.path_ok("/usr/bin/yakuake")    # Ausschluss schlägt Basis-Glob
    assert a.path_ok("/usr/bin/plasmashell")


def test_enforce_falls_back_to_audit_without_base():
    a = FakeAgent("enforce")
    w, killed, _ = make_watcher(a, {2: proc("/usr/bin/konsole", pid=2, uid=a.uid)})
    w.base_exact = frozenset()   # Berechnung fehlgeschlagen
    asyncio.run(w.scan())
    assert killed == [] and w.pending[0].audit is True


def test_exec_after_allowed_program_is_checked_again():
    """Regression: Apps aus Plasma starten als systemd-Unit: systemd-executor (erlaubt) -> exec(kate)
    mit gleicher PID und Startzeit. Der zweite exec muss erneut geprüft werden."""
    a = FakeAgent("enforce")
    ps = {7: proc("/usr/bin/kwin_wayland", pid=7, uid=a.uid, start=42)}
    w, killed, _ = make_watcher(a, ps)
    asyncio.run(w.scan())
    assert killed == []
    ps[7] = proc("/usr/bin/kate", pid=7, uid=a.uid, start=42)          # exec: gleiche PID/Startzeit
    asyncio.run(w.check_pid(7))                                        # Netlink-Event
    assert killed == [(7, signal.SIGKILL)]


def test_interpreter_exec_with_new_args_is_checked_again():
    a = FakeAgent("enforce")
    ok = proc("/usr/bin/python3.14", ["python3", "/usr/lib/kidctl/kidctl-tray-main.py"], pid=8, uid=a.uid)
    ps = {8: ok}
    w, killed, _ = make_watcher(a, ps)
    asyncio.run(w.scan())
    ps[8] = proc("/usr/bin/python3.14", ["python3", "-c", "boese"], pid=8, uid=a.uid)
    asyncio.run(w.scan())
    assert killed == [(8, signal.SIGKILL)]


def test_empty_argv_during_exec_is_reread(monkeypatch):
    """Regression: Tray-Wrapper (/bin/sh /usr/bin/kidctl-tray) wurde beendet, weil die Argumente
    mitten im exec() leer gelesen wurden und die Shell dadurch als interaktiv galt."""
    import kidctl_agent.watcher as wmod
    monkeypatch.setattr(wmod, "EMPTY_ARGV_DELAY", 0)
    a = FakeAgent("enforce")
    reads = iter([proc("/usr/bin/bash", (), pid=9, uid=a.uid),
                  proc("/usr/bin/bash", ["/bin/sh", "/usr/bin/kidctl-tray"], pid=9, uid=a.uid)])
    w, killed, _ = make_watcher(a, {})
    w.read = lambda pid: next(reads)
    asyncio.run(w.check_pid(9))
    assert killed == []


def test_persistently_empty_argv_shell_still_blocked(monkeypatch):
    import kidctl_agent.watcher as wmod
    monkeypatch.setattr(wmod, "EMPTY_ARGV_DELAY", 0)
    a = FakeAgent("enforce")
    w, killed, _ = make_watcher(a, {9: proc("/usr/bin/bash", (), pid=9, uid=a.uid)})
    asyncio.run(w.check_pid(9))
    assert killed == [(9, signal.SIGKILL)]


def test_read_proc_rereads_when_exe_changes(monkeypatch):
    exes = iter(["/usr/lib/systemd/systemd-executor", "/usr/bin/bash", "/usr/bin/bash", "/usr/bin/bash"])
    monkeypatch.setattr(procs, "_exe", lambda pid: next(exes))
    seen = []
    monkeypatch.setattr(procs, "_read_once", lambda pid, exe: seen.append(exe) or proc(exe, ["sh"], pid=pid))
    info = procs.read_proc(5)
    assert info.exe == "/usr/bin/bash" and seen == ["/usr/lib/systemd/systemd-executor", "/usr/bin/bash"]


QI_GROUPS = """Name            : powerdevil
Groups          : plasma
Depends On      : kidletime

Name            : kidletime
Groups          : kf6
Depends On      : None

Name            : baloo
Groups          : kf6  kf6-extra
Depends On      : None

Name            : firefox
Groups          : None
Depends On      : None
"""


def test_group_members():
    assert programs.group_members(QI_GROUPS, ["plasma"]) == ["powerdevil"]
    assert programs.group_members(QI_GROUPS, ["kf6"]) == ["baloo", "kidletime"]
    assert programs.group_members(QI_GROUPS, []) == []
    deps, provides = programs.parse_pacman_qi(QI_GROUPS)
    assert programs.closure(deps, provides, programs.group_members(QI_GROUPS, ["plasma"])) == {
        "powerdevil", "kidletime"}


def test_base_file_groups(tmp_path):
    f = tmp_path / "base.txt"
    f.write_text("group:plasma\npkg:kwin\n")
    spec = programs.load_base(f)
    assert spec.groups == ["plasma"] and spec.packages == ["kwin"]


def test_compute_base_uses_groups(monkeypatch, tmp_path):
    from kidctl_agent import watcher
    exe = tmp_path / "powerdevil"
    exe.write_text("")
    exe.chmod(0o755)
    calls = []

    def fake_run(cmd, **kw):
        import subprocess
        calls.append(cmd)
        out = QI_GROUPS if cmd[1] == "-Qi" else f"{exe}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(watcher.subprocess, "run", fake_run)
    monkeypatch.setattr(watcher, "base_paths", lambda files, app_exes, excludes, dirs=None: frozenset(files))
    monkeypatch.setattr(watcher, "service_exes", lambda: {"/usr/bin/kdeconnectd"})
    assert watcher._compute_base([], set(), [], ["plasma"]) == {str(exe), "/usr/bin/kdeconnectd"}
    assert calls[1][:2] == ["pacman", "-Qlq"] and set(calls[1][2:]) == {"powerdevil", "kidletime"}
