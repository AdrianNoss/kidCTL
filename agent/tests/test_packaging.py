import re
from pathlib import Path

import kidctl_agent

ROOT = Path(__file__).resolve().parents[2]


def test_pkgbuild_version_matches_agent():
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    assert re.search(r"^pkgver=(\S+)", pkgbuild, re.M).group(1) == kidctl_agent.__version__


def test_unit_orders_agent_before_logins():
    unit = (ROOT / "packaging/arch/kidctl-agent.service").read_text()
    assert "Type=notify" in unit and "systemd-user-sessions.service" in unit
    deps = [line for line in unit.splitlines() if line.startswith(("After=", "Wants=", "Requires="))]
    assert not any("network-online" in line for line in deps)  # offline keine Login-Verzögerung


def test_base_list_contains_agent_helpers():
    """Programme, die der Agent mit der UID des Kindes startet, dürfen nie gesperrt werden."""
    from kidctl_agent import programs
    spec = programs.load_base(ROOT / "packaging/arch/base-programs.txt")
    a = programs.Allowed(spec.globs, set(), frozenset(), spec.excludes)
    for helper in ("/usr/bin/notify-send", "/usr/bin/busctl", "/usr/lib/kidctl/kidctl-tray-main.py"):
        assert a.path_ok(helper), helper
    assert "plasma-desktop" in spec.packages and {"plasma", "kf6"} <= set(spec.groups)
    assert not a.path_ok("/usr/bin/konsole")


def test_base_list_excludes_socat():
    from kidctl_agent import programs
    spec = programs.load_base(ROOT / "packaging/arch/base-programs.txt")
    base = programs.base_paths(["/usr/bin/socat", "/usr/bin/socat1", "/usr/bin/kded6"], set(), spec.excludes)
    assert base == {"/usr/bin/kded6"}


def test_split_packages_and_gnome_base():
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    assert "pkgname=(kidctl kidctl-plasma kidctl-gnome)" in pkgbuild
    for name in ("kidctl", "kidctl-plasma", "kidctl-gnome"):
        assert f"package_{name}()" in pkgbuild
    # Desktop-Pakete legen die Markierung an, die die Integritätsprüfung erwartet
    from kidctl_agent import integrity
    for desktop, (_prog, marker) in integrity.DESKTOP_PACKAGES.items():
        assert f'"$pkgdir{marker}"' in pkgbuild, desktop
    from kidctl_agent import programs
    spec = programs.load_base(ROOT / "packaging/arch/base-programs.txt")
    assert "gnome" in spec.groups and "gdm" in spec.packages
    base = programs.base_paths(["/usr/bin/kgx", "/usr/bin/gnome-terminal", "/usr/lib/gnome-terminal-server",
                                "/usr/bin/gjs", "/usr/lib/gsd-power"], set(), spec.excludes)
    assert base == {"/usr/lib/gsd-power"}   # Terminals nie, gjs als Interpreter nie
    desktop = (ROOT / "packaging/gnome/kidctl.desktop").read_text()
    assert "Exec=/usr/bin/kidctl-tray --show" in desktop and "OnlyShowIn=GNOME;" in desktop
