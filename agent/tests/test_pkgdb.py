"""Paketdatenbank: dpkg (Debian/Ubuntu) neben pacman."""

import os

from kidctl_agent import pkgdb
from kidctl_agent.programs import closure, package_dirs

DPKG = "\n".join([
    "ii \tubuntu-desktop-minimal\t\tgdm3, gnome-shell\tnautilus | thunar, snapd:any (>= 2.6)\t",
    "ii \tgdm3\t\tlibc6 (>= 2.34), gnome-session-bin\t\t",
    "ii \tgnome-shell\tlibc6\tmutter-common, gnome-shell-common (= 46.0-0ubuntu6)\t\tnotification-daemon",
    "ii \tgnome-session-bin\t\t\t\t",
    "ii \tnautilus\t\t\t\t",
    "ii \tsnapd\t\t\t\t",
    "rc \tentfernt\t\t\t\t",
    "ii \tlibc6:amd64\t\t\t\t",
    "ii \tgrep\t\t\t\t\tyes",
    "it \tlibglib2.0-0t64:amd64\t\t\t\t",          # Trigger ausstehend (apt läuft gerade)
    "iU \thalb\t\t\t\t",
])


def test_parse_dpkg_query_and_closure():
    deps, provides, groups = pkgdb.parse_dpkg_query(DPKG)
    assert "entfernt" not in deps and "halb" not in deps and groups == {"grep": {"essential"}}
    assert "libglib2.0-0t64" in deps
    assert deps["ubuntu-desktop-minimal"] == ["gdm3", "gnome-shell", "nautilus", "thunar", "snapd"]
    assert provides["notification-daemon"] == "gnome-shell"
    # Empfehlungen zählen, nicht installierte Alternativen (thunar) werden übersprungen
    assert closure(deps, provides, ["ubuntu-desktop-minimal"]) == {
        "ubuntu-desktop-minimal", "gdm3", "gnome-shell", "gnome-session-bin", "nautilus", "snapd", "libc6"}


def test_dpkg_files_and_listing(tmp_path):
    info, root = tmp_path / "info", tmp_path / "root"
    info.mkdir()
    (root / "usr" / "bin").mkdir(parents=True)
    os.symlink("usr/bin", root / "bin")                # usrmerge
    (root / "sbin").mkdir()                             # nicht zusammengeführt
    (info / "nautilus.list").write_text("/.\n/usr\n/usr/bin\n/usr/bin/nautilus\n/usr/lib/nautilus\n"
                                        "/usr/lib/nautilus/ext.so\n")
    (info / "coreutils:amd64.list").write_text("/bin\n/bin/ls\n/sbin/x\n")
    (info / "nautilus.md5sums").write_text("egal")
    db = pkgdb.Dpkg(str(info), str(root))
    assert db.merged == {"/bin": "/usr/bin"}
    assert sorted(db.files(["coreutils", "fehlt"])) == ["/sbin/x", "/usr/bin", "/usr/bin/ls"]
    listing = db.listing().splitlines()
    assert "nautilus /usr/lib/nautilus/" in listing and "nautilus /usr/bin/nautilus" in listing
    assert package_dirs(db.listing(), {"/usr/bin/nautilus"}) == {"/usr/bin/nautilus": ("/usr/lib/nautilus",)}


def test_detect(monkeypatch, tmp_path):
    monkeypatch.setattr(pkgdb.Pacman, "db_path", str(tmp_path / "pacman"))
    monkeypatch.setattr(pkgdb.Dpkg, "db_path", str(tmp_path / "status"))
    assert pkgdb.detect() is None
    (tmp_path / "status").write_text("")
    assert isinstance(pkgdb.detect(), pkgdb.Dpkg)
    (tmp_path / "pacman").mkdir()
    assert isinstance(pkgdb.detect(), pkgdb.Pacman)
