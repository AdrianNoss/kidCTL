"""Snap-Apps: Freigabe je Snap-Name, nur echte Snap-Prozesse (Image + eigener Mount-Namespace)."""

import os

from kidctl_agent import apps, procs
from kidctl_agent.programs import Allowed, classify
from kidctl_agent.procs import ProcInfo

MOUNTINFO = """\
25 1 259:2 / / rw,relatime shared:1 - ext4 /dev/vda1 rw
101 25 7:3 / /snap/firefox/5361 ro,nodev,relatime shared:60 - squashfs /dev/loop3 ro
102 25 7:4 / /snap/core22/1981 ro,nodev,relatime shared:61 - squashfs /dev/loop4 ro
103 25 0:55 / /run/snapd/ns rw - tmpfs tmpfs rw
"""
SNAP_DEV = os.makedev(7, 3)


def proc(exe, snap=None, foreign=True, dev=SNAP_DEV):
    return ProcInfo(pid=9, uid=1001, ppid=1, start=1, exe=exe, argv=(exe,), tty=False, flatpak_id=None,
                    mnt_foreign=foreign, exe_id=(dev, 42), snap_name=snap)


def test_snap_devices_and_cgroup(tmp_path, monkeypatch):
    mi = tmp_path / "mountinfo"
    mi.write_text(MOUNTINFO)
    assert procs.snap_devices(str(mi)) == {os.makedev(7, 3), os.makedev(7, 4)}
    cg = "0::/user.slice/user-1001.slice/user@1001.service/app.slice/snap.firefox.firefox-1a2b.scope\n"
    assert procs.SNAP_UNIT.search(cg).group("name") == "firefox"
    assert procs.SNAP_UNIT.search("0::/user.slice/.../snap.snapd-desktop-integration.snapd-desktop-integration"
                                  ".service").group("name") == "snapd-desktop-integration"
    assert procs.SNAP_UNIT.search("0::/user.slice/app.slice/app-gnome-firefox-123.scope") is None


def test_classify_snap(tmp_path, monkeypatch):
    mi = tmp_path / "mountinfo"
    mi.write_text(MOUNTINFO)
    monkeypatch.setattr(procs, "HOST_MOUNTINFO", str(mi))
    monkeypatch.setattr(procs, "_snap_devs", set())
    monkeypatch.setattr(procs.snap_file, "__defaults__", (str(mi),))
    ff = proc("/snap/firefox/5361/usr/lib/firefox/firefox", "firefox")
    assert classify(ff, Allowed([], {"snap:firefox"})) is None
    v = classify(ff, Allowed([], set()))
    assert (v.key, v.name) == ("snap:firefox", "firefox")
    # Shell aus dem Basis-Snap innerhalb der Firefox-Sandbox gehört zur App
    assert classify(proc("/usr/bin/bash", "firefox", dev=os.makedev(7, 4)), Allowed([], {"snap:firefox"})) is None
    # Selbst angelegte Unit „snap.firefox…“ mit Programm aus dem System: kein Snap -> normale Prüfung
    fake = proc("/usr/bin/python3", "firefox", foreign=False, dev=os.makedev(259, 2))
    assert classify(fake, Allowed([], {"snap:firefox"})).key.startswith("exe:")
    fake_ns = proc("/usr/bin/python3", "firefox", foreign=True, dev=os.makedev(259, 2))
    assert not procs.snap_file(fake_ns, str(mi))


def test_snap_in_catalog(tmp_path):
    f = tmp_path / "firefox_firefox.desktop"
    f.write_text("[Desktop Entry]\nType=Application\nName=Firefox Web Browser\nX-SnapInstanceName=firefox\n"
                 "Exec=env BAMF_DESKTOP_FILE_HINT=/var/lib/snapd/desktop/applications/firefox_firefox.desktop "
                 "/snap/bin/firefox %u\n")
    e = apps.parse_desktop(f)
    assert (e.key, e.name) == ("snap:firefox", "Firefox Web Browser")
    assert "/var/lib/snapd/desktop/applications" in apps.APP_DIRS
