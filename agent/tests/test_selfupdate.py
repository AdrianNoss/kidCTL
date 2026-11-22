"""Update auf Knopfdruck (Linux): nur eigene Pakete aus der lokal eingerichteten, signierten Quelle; Ablauf im Kern."""

import asyncio
import subprocess

from kidctl_common import protocol as p

from kidctl_agent import selfupdate as su
from test_agent import make_agent

PACMAN_CONF = """[options]
HoldPkg = pacman glibc
[core]
Include = /etc/pacman.d/mirrorlist
[extra]
Include = /etc/pacman.d/mirrorlist

# kidctl – Parental Control (verwaltet von kidctl-repo-client.sh)
[kidctl]
SigLevel = Never
Server = https://app.kidctl.de/repo   # Kommentar
Server = file:///tmp/evil
Include = /etc/pacman.d/evil
"""


class FakeRun:
    """Gemockter Paketmanager: ``installed`` Pakete; Ergebnis des systemd-run-Aufrufs ``rc``."""

    def __init__(self, installed=("kidctl", "kidctl-plasma"), rc=0, journal=""):
        self.installed, self.rc, self.journal, self.calls = set(installed), rc, journal, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["pacman", "-Q"]:
            return subprocess.CompletedProcess(cmd, 0 if cmd[2] in self.installed else 1, "", "")
        if cmd[0] == "dpkg-query":
            ok = cmd[-1] in self.installed
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, "install ok installed" if ok else "", "")
        if cmd[0] == "journalctl":
            return subprocess.CompletedProcess(cmd, 0, self.journal, "")
        if cmd[0] == "systemd-run":
            return subprocess.CompletedProcess(cmd, self.rc, "", "")
        raise AssertionError(cmd)


def _arch(tmp_path, run, conf=PACMAN_CONF):
    (tmp_path / "pacman.conf").write_text(conf)
    return su.SelfUpdater(run=run, which=lambda n: "/usr/bin/pacman" if n == "pacman" else None,
                          pacman_conf=tmp_path / "pacman.conf", work_dir=tmp_path / "run")


def test_pacman_config_only_kidctl_repo_with_signatures():
    conf = su.pacman_config(PACMAN_CONF)
    assert "[core]" not in conf and "[extra]" not in conf and "Include" not in conf
    assert "SigLevel = Never" not in conf and conf.count("SigLevel = Required DatabaseRequired") == 2
    assert su.repo_servers(PACMAN_CONF) == ["https://app.kidctl.de/repo"]   # file:// u. Ä. verworfen


def test_arch_update_runs_pacman_in_transient_unit(tmp_path):
    run = FakeRun()
    assert _arch(tmp_path, run)() == ("current", "")   # kehrt zurück = nichts zu tun (sonst Neustart)
    cmd = run.calls[-1]
    assert cmd[:2] == ["systemd-run", "--unit=kidctl-update"] and "--wait" in cmd
    i = cmd.index("--")
    assert cmd[i + 1:] == ["pacman", "--config", str(tmp_path / "run" / "pacman.conf"), "-Sy", "--needed",
                           "--noconfirm", "kidctl", "kidctl-plasma"]   # nur installierte eigene Pakete, kein -u
    assert (tmp_path / "run" / "pacman.conf").stat().st_mode & 0o077 == 0


def test_arch_failure_reports_journal(tmp_path):
    run = FakeRun(rc=1, journal="error: failed to synchronize kidctl\nerror: failed to commit transaction\n")
    state, detail = _arch(tmp_path, run)()
    assert state == "failed" and "failed to commit transaction" in detail and len(detail) <= 300


def test_arch_without_repo_or_package(tmp_path):
    assert _arch(tmp_path, FakeRun(), conf="[core]\nInclude = x\n")() == \
        ("failed", "kidCTL-Repository nicht in /etc/pacman.conf eingetragen")
    run = FakeRun(installed=())
    assert _arch(tmp_path, run)()[0] == "failed" and not any(c[0] == "systemd-run" for c in run.calls)


def test_debian_update_only_kidctl_source(tmp_path):
    src = tmp_path / "kidctl.sources"
    run = FakeRun(installed=("kidctl", "kidctl-gnome"))
    upd = su.SelfUpdater(run=run, which=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None, apt_sources=src)
    assert upd()[0] == "failed"                               # Quelle fehlt
    src.write_text("Types: deb\nURIs: https://app.kidctl.de/repo/deb\nSuites: stable\nComponents: main\n")
    assert upd()[0] == "failed"                               # ohne Signed-By nicht
    src.write_text(src.read_text() + "Signed-By: /etc/apt/keyrings/kidctl.asc\n")
    assert upd() == ("current", "")
    cmd = run.calls[-1]
    assert "--setenv=DEBIAN_FRONTEND=noninteractive" in cmd
    script = cmd[-1]
    assert f"Dir::Etc::sourcelist={src}" in script and "Dir::Etc::sourceparts=-" in script
    assert script.endswith("--only-upgrade -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "
                           "kidctl kidctl-gnome")


def test_core_update_now_debounced_and_reported(monkeypatch):
    clock = [100.0]
    a, _ = make_agent(monkeypatch, clock)
    sent, calls = [], []

    async def send(msg):
        sent.append(msg)
        return True
    a.conn.send = send

    async def fake_update():
        calls.append(1)
        return "failed", "x" * 500
    a._self_update = fake_update

    async def scenario():
        await a.on_message(p.UpdateNow())
        await a.on_message(p.UpdateNow())   # läuft bzw. gerade angestoßen -> ignoriert
        await a._update_task
        await a.on_message(p.UpdateNow())   # innerhalb der Sperrzeit
        clock[0] += 61
        await a.on_message(p.UpdateNow())
        await a._update_task
    asyncio.run(scenario())
    assert len(calls) == 2
    assert [m.state for m in sent] == ["running", "failed", "running", "failed"] and len(sent[1].detail) == 300


def test_hello_announces_feature():
    import json

    from kidctl_agent.agent import Agent
    from kidctl_agent.connection import Connection

    conn = Connection({"server_url": "https://x", "token": "t"}, Agent)
    assert json.loads(conn._hello())["features"] == [p.FEATURE_SELF_UPDATE]
