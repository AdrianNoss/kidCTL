"""noexec-Homes, Steam-Bibliothek und Steam-Regeln (ohne echte Mounts)."""

import getpass
import os
import pwd
import subprocess

from kidctl_agent import kidmounts
from kidctl_agent.kidmounts import DESCRIPTION, KidMounts, safe_home
from kidctl_agent.programs import Allowed, classify
from test_programs import proc

ME = getpass.getuser()
MY_HOME = pwd.getpwnam(ME).pw_dir


class FakeSystemd:
    """Simuliert systemd-mount/-umount/-escape/systemctl show."""

    def __init__(self, active=()):
        self.active = set(active)  # unsere aktiven Mounts
        self.calls = []

    def __call__(self, *cmd):
        self.calls.append(cmd)
        out = ""
        if cmd[0] == "systemd-escape":
            out = cmd[-1].strip("/").replace("/", "-") + ".mount\n"
        elif cmd[:2] == ("systemctl", "show"):
            path = "/" + cmd[2].removesuffix(".mount").replace("-", "/")
            out = f"{DESCRIPTION}\nactive\n" if path in self.active else "\ninactive\n"
        elif cmd[0] == "systemd-mount":
            self.active.add(cmd[-1])
        elif cmd[0] == "systemd-umount":
            self.active.discard(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_safe_home_rules(monkeypatch):
    monkeypatch.setattr(kidmounts, "HOME_PARENT", os.path.dirname(MY_HOME))
    assert safe_home(ME) == MY_HOME
    assert safe_home("gibt-es-nicht-xyz") is None
    monkeypatch.setattr(kidmounts, "HOME_PARENT", "/anderswo")
    assert safe_home(ME) is None


def test_sync_mounts_and_unmounts_only_ours(monkeypatch):
    monkeypatch.setattr(kidmounts, "HOME_PARENT", os.path.dirname(MY_HOME))
    monkeypatch.setattr(kidmounts.os, "listdir", lambda p: [os.path.basename(MY_HOME), "fremd"])
    sysd = FakeSystemd()
    m = KidMounts(run=sysd, mounts=lambda: set())
    m.sync({ME})
    mount = [c for c in sysd.calls if c[0] == "systemd-mount"]
    assert mount == [("systemd-mount", "--options=bind,noexec,nosuid,nodev", f"--description={DESCRIPTION}",
                      MY_HOME, MY_HOME)]
    # Schon eingehängt -> nichts; Kind nicht mehr zugeordnet -> nur unseren Mount aushängen
    sysd.calls.clear()
    m2 = KidMounts(run=sysd, mounts=lambda: {MY_HOME})
    m2.sync({ME})
    assert not [c for c in sysd.calls if c[0] in ("systemd-mount", "systemd-umount")]
    m2.sync(set())
    assert [c for c in sysd.calls if c[0] == "systemd-umount"] == [("systemd-umount", MY_HOME)]


def test_ensure_steam_creates_library_and_links_as_user(tmp_path, monkeypatch):
    monkeypatch.setattr(kidmounts, "HOME_PARENT", os.path.dirname(MY_HOME))
    monkeypatch.setattr(kidmounts, "STEAM_INSTALLED", tmp_path)       # „Steam installiert“
    monkeypatch.setattr(kidmounts, "STEAM_ROOT", tmp_path / "steam")
    sysd = FakeSystemd()
    KidMounts(run=sysd).ensure_steam({ME})
    lib = tmp_path / "steam" / ME
    assert lib.is_dir() and oct(lib.stat().st_mode & 0o777) == "0o700"
    link = [c for c in sysd.calls if c[0] == "setpriv"][0]
    assert f"--reuid={os.getuid()}" in link and link[-2:] == (MY_HOME, str(lib))  # als User, nicht root


# ---------- Steam-Regeln ----------

ROOT = "/srv/steam/kind/"
STEAM = "exe:/usr/lib/steam/steam"
BOOT = proc("/usr/bin/bash", ["/bin/bash", "/usr/lib/steam/steam"], pid=10)
STEAMSH = proc("/usr/bin/bash", ["/bin/bash", ROOT + "steam.sh"], pid=11)
CLIENT = proc(ROOT + "ubuntu12_32/steam", pid=12)


def steam_allowed(*keys):
    return Allowed(["/usr/lib/kidctl/*"], set(keys), frozenset({"/usr/bin/kwin_wayland"}), [], steam_root=ROOT)


def test_steam_game_needs_steam_and_ancestry():
    game = proc(ROOT + "steamapps/common/Spiel/spiel", pid=20)
    on = steam_allowed(STEAM)
    assert on.steam_enabled
    assert classify(game, on, ancestors=lambda: [CLIENT, STEAMSH, BOOT]) is None
    # direkt aus Dolphin gestartet (keine Steam-Abstammung)
    dolphin = proc("/usr/bin/dolphin", pid=5)
    v = classify(game, on, ancestors=lambda: [dolphin])
    assert v.key == f"exe:{ROOT}steamapps/common/Spiel/spiel" and "über Steam" in v.detail
    # Steam nicht freigegeben
    v = classify(game, steam_allowed(), ancestors=lambda: [CLIENT, BOOT])
    assert "nicht freigegeben" in v.detail


def test_steam_bootstrap_and_client_chain():
    on = steam_allowed(STEAM)
    assert classify(BOOT, on) is None                                           # Starter freigegeben
    assert classify(STEAMSH, on, ancestors=lambda: [BOOT]) is None              # steam.sh vom Starter
    assert classify(CLIENT, on, ancestors=lambda: [STEAMSH, BOOT]) is None      # Client
    # Regression: Der Starter ruft steam.sh per exec auf -> kein Steam-Vorfahre mehr
    assert classify(STEAMSH, on, ancestors=lambda: [proc("/usr/lib/systemd/systemd")]) is None
    # Einstiegspunkte nur, wenn Steam freigegeben ist
    assert classify(STEAMSH, steam_allowed(), ancestors=lambda: []) is not None
    assert classify(CLIENT, steam_allowed(), ancestors=lambda: []) is not None


def test_steam_descendants_outside_library_only_when_enabled():
    pv = proc("/usr/lib/pressure-vessel/from-host/bin/pv-bwrap", pid=30)   # Laufzeit-Container
    assert classify(pv, steam_allowed(STEAM), ancestors=lambda: [CLIENT, BOOT]) is None
    assert classify(pv, steam_allowed(), ancestors=lambda: [CLIENT, BOOT]) is not None
    # Ahnenkette wird nur bei Bedarf gelesen (erlaubte Basis-Programme: gar nicht)
    called = []
    classify(proc("/usr/bin/kwin_wayland"), steam_allowed(STEAM), ancestors=lambda: called.append(1) or [])
    assert called == []
