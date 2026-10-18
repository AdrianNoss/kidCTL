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
    m2 = KidMounts(run=sysd, mounts=lambda: {MY_HOME}, in_use=lambda h: False)
    m2.sync({ME})
    assert not [c for c in sysd.calls if c[0] in ("systemd-mount", "systemd-umount")]
    m2.sync(set())
    assert [c for c in sysd.calls if c[0] == "systemd-umount"] == [("systemd-umount", MY_HOME)]


def test_unmount_deferred_while_owner_logged_in(monkeypatch):
    """Programmkontrolle aus, während das Kind angemeldet ist: Aushängen erst nach der Abmeldung
    (sofortiges Aushängen hatte die Plasma-Sitzung abgeschossen -> schwarzer Bildschirm)."""
    monkeypatch.setattr(kidmounts, "HOME_PARENT", os.path.dirname(MY_HOME))
    monkeypatch.setattr(kidmounts.os, "listdir", lambda p: [os.path.basename(MY_HOME)])
    sysd = FakeSystemd(active={MY_HOME})
    logged_in = {"yes": True}
    m = KidMounts(run=sysd, mounts=lambda: {MY_HOME}, in_use=lambda h: logged_in["yes"])
    m.sync(set())
    assert not [c for c in sysd.calls if c[0] == "systemd-umount"]
    assert m.deferred == {MY_HOME} and not m.releasable()
    logged_in["yes"] = False                      # abgemeldet
    assert m.releasable()
    m.sync(set())
    assert [c for c in sysd.calls if c[0] == "systemd-umount"] == [("systemd-umount", MY_HOME)]
    assert m.deferred == set() and not m.releasable()


def test_deferred_cleared_when_wanted_again(monkeypatch):
    monkeypatch.setattr(kidmounts, "HOME_PARENT", os.path.dirname(MY_HOME))
    monkeypatch.setattr(kidmounts.os, "listdir", lambda p: [os.path.basename(MY_HOME)])
    m = KidMounts(run=FakeSystemd(active={MY_HOME}), mounts=lambda: {MY_HOME}, in_use=lambda h: True)
    m.sync(set())
    assert m.deferred == {MY_HOME}
    m.sync({ME})                                   # wieder eingeschaltet -> nichts mehr aufgeschoben
    assert m.deferred == set()


def test_home_in_use_checks_runtime_dir(tmp_path, monkeypatch):
    assert kidmounts.home_in_use(str(tmp_path / "fehlt")) is False
    exists = []
    monkeypatch.setattr(kidmounts.os.path, "exists", lambda p: exists.append(p) or True)
    assert kidmounts.home_in_use(str(tmp_path)) is True and exists == [f"/run/user/{os.getuid()}"]


def test_ensure_steam_creates_library_and_links_as_user(tmp_path, monkeypatch):
    monkeypatch.setattr(kidmounts, "HOME_PARENT", os.path.dirname(MY_HOME))
    monkeypatch.setattr(kidmounts, "STEAM_INSTALLED", tmp_path)       # „Steam installiert“
    monkeypatch.setattr(kidmounts, "STEAM_ROOT", tmp_path / "steam")
    sysd = FakeSystemd()
    KidMounts(run=sysd).ensure_libraries({ME})
    lib = tmp_path / "steam" / ME
    assert lib.is_dir() and oct(lib.stat().st_mode & 0o777) == "0o700"
    link = [c for c in sysd.calls if c[0] == "setpriv"][0]
    assert f"--reuid={os.getuid()}" in link and link[-4:] == (MY_HOME, ".local/share/Steam", str(lib), "0")  # als User, nicht root


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
    assert v.key == "exe:/usr/bin/steam" and v.name == "Steam" and "nicht freigegeben – spiel" == v.detail


def test_steam_launcher_helpers_reported_as_steam_when_not_allowed():
    """Ohne Steam-Freigabe erscheinen Hilfsprogramme des Starters (cmp, srt-logger …) als „Steam“."""
    cmp = proc("/usr/bin/cmp", pid=40)
    v = classify(cmp, steam_allowed(), ancestors=lambda: [BOOT])
    assert (v.key, v.name, v.detail) == ("exe:/usr/bin/steam", "Steam", "nicht freigegeben – cmp")
    logger = proc(ROOT + "ubuntu12_32/steam-runtime/usr/libexec/steam-runtime-tools-0/srt-logger", pid=41)
    assert classify(logger, steam_allowed(), ancestors=lambda: [STEAMSH, BOOT]).key == "exe:/usr/bin/steam"
    # ohne Steam-Abstammung bleibt cmp ein gewöhnlicher Eintrag
    assert classify(cmp, steam_allowed(), ancestors=lambda: []).key == "exe:/usr/bin/cmp"


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


# ---------- Umzug eines vorhandenen Steam-Ordners ----------

def _steam_home(tmp_path, monkeypatch):
    """Fake-Home (eigener User) mit echtem Steam-Ordner, Bibliothek unter tmp/srv-steam."""
    home = tmp_path / "home" / ME
    (home / ".local/share/Steam/ubuntu12_32").mkdir(parents=True)
    (home / ".local/share/Steam/steam.sh").write_text("#!/bin/sh\n")
    root = tmp_path / "srv-steam"
    monkeypatch.setattr(kidmounts, "STEAM_INSTALLED", tmp_path)
    monkeypatch.setattr(kidmounts, "STEAM_ROOT", root)
    monkeypatch.setattr(kidmounts, "STEAM_ROOT_OWNER", os.getuid())
    monkeypatch.setattr(kidmounts, "safe_home", lambda u: str(home) if u == ME else None)
    return home, root / ME


def _real_link(*cmd):
    """setpriv-Aufruf ohne Rechtewechsel ausführen (Tests laufen als eigener User)."""
    if cmd[0] == "setpriv":
        cmd = cmd[cmd.index("--") + 1:]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_existing_steam_dir_moved_after_logout(tmp_path, monkeypatch):
    home, lib = _steam_home(tmp_path, monkeypatch)
    logged_in = {"yes": True}
    m = KidMounts(run=_real_link, in_use=lambda h: logged_in["yes"])
    m.ensure_libraries({ME})
    assert (home / ".local/share/Steam").is_dir() and not (home / ".local/share/Steam").is_symlink()
    assert (ME, ".local/share/Steam") in m.lib_pending and "nach der Abmeldung" in m.lib_problems[("steam", ME)]
    logged_in["yes"] = False
    assert m.releasable()
    m.ensure_libraries({ME})
    link = home / ".local/share/Steam"
    assert link.is_symlink() and os.readlink(link) == str(lib)
    assert (lib / "steam.sh").exists() and (lib / "ubuntu12_32").is_dir()
    assert oct(lib.stat().st_mode & 0o777) == "0o700"
    assert m.lib_problems == {} and m.lib_pending == set() and not m.releasable()


def test_steam_move_refuses_symlinked_path_components(tmp_path, monkeypatch):
    """Das Kind ersetzt .local durch einen Verweis auf ein fremdes Verzeichnis -> nichts wird bewegt."""
    home, lib = _steam_home(tmp_path, monkeypatch)
    victim = tmp_path / "fremd"
    (victim / "share/Steam").mkdir(parents=True)
    import shutil
    shutil.rmtree(home / ".local")
    (home / ".local").symlink_to(victim)
    m = KidMounts(run=lambda *c: subprocess.CompletedProcess(c, 3, "", ""), in_use=lambda h: False)
    m.ensure_libraries({ME})
    assert (victim / "share/Steam").is_dir()          # unangetastet
    assert lib.is_dir() and not any(lib.iterdir())    # Ziel weiterhin leer
    assert "nicht verschiebbar" in m.lib_problems[("steam", ME)]


def test_steam_move_cross_device_warns(tmp_path, monkeypatch):
    import errno as _errno
    home, lib = _steam_home(tmp_path, monkeypatch)
    real_rename = os.rename

    def exdev(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src == "Steam":
            raise OSError(_errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
    monkeypatch.setattr(kidmounts.os, "rename", exdev)
    m = KidMounts(run=_real_link, in_use=lambda h: False)
    m.ensure_libraries({ME})
    assert "andere Partition" in m.lib_problems[("steam", ME)]
    assert (home / ".local/share/Steam").is_dir() and lib.is_dir()    # nichts verloren, Ziel wieder da


def test_steam_problems_in_integrity():
    from kidctl_agent import integrity
    checks = integrity.check_libraries({("steam", "mika"): "~/.local/share/Steam liegt im Home",
                                       ("heroic", "mika"): "~/Games/Heroic liegt im Home"})
    assert [(c.id, c.label, c.ok) for c in checks] == [("heroic:mika", "Heroic-Spieleordner von mika", False),
                                                      ("steam:mika", "Steam-Bibliothek von mika", False)]
