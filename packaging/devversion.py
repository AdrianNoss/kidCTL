#!/usr/bin/env python3
"""Vorab-Version für den Bau aus dem Branch ``dev`` (Paketkanal der Testinstanz).

    devversion.py show            Vorab-Version des aktuellen Stands ausgeben, z. B. 0.48.0dev312
    devversion.py set [VERSION] [--apply]
                                  Version in agent/kidctl_agent/__init__.py und packaging/arch/PKGBUILD eintragen
                                  (Standard: Ergebnis von „show“) – nur im Bau-Checkout (GitHub Actions), nie committen

Schema: nächste Minor-Version nach ``__version__`` + ``dev`` + Anzahl der Commits (``git rev-list --count HEAD``;
steigt auf ``dev`` mit jedem Fast-Forward/Merge). pacman sortiert ``0.48.0dev312`` vor ``0.48.0``, Debian bekommt
``0.48.0~dev312`` (``build-deb.py``), der Windows-Installer die Dateiversion ``0.47.65535.312``.
Ohne --apply nur Anzeige (Dry-Run).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
from kidctl_common.version import PATTERN, is_prerelease, next_dev  # noqa: E402

INIT = ROOT / "agent" / "kidctl_agent" / "__init__.py"
PKGBUILD = ROOT / "packaging" / "arch" / "PKGBUILD"
VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"', re.M)
PKGVER_LINE = re.compile(r"^pkgver=\S+", re.M)


def base_version() -> str:
    return VERSION_LINE.search(INIT.read_text()).group(1)


def dev_version() -> str:
    base = base_version()
    if is_prerelease(base):
        sys.exit(f"__version__ ist schon eine Vorab-Version ({base})")
    count = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD"], check=True,
                           capture_output=True, text=True).stdout.strip()
    return next_dev(base, int(count))


def set_version(ver: str, apply: bool) -> None:
    if not (re.fullmatch(PATTERN, ver) and is_prerelease(ver)):
        sys.exit(f"keine Vorab-Version: {ver!r} (erwartet z. B. 0.48.0dev312)")
    init = VERSION_LINE.sub(f'__version__ = "{ver}"', INIT.read_text(), count=1)
    pkgbuild = PKGVER_LINE.sub(f"pkgver={ver}", PKGBUILD.read_text(), count=1)
    print(f"Version {base_version()} -> {ver}: {INIT.relative_to(ROOT)}, {PKGBUILD.relative_to(ROOT)}")
    if not apply:
        print("Dry-Run – mit --apply eintragen.")
        return
    INIT.write_text(init, newline="\n")   # auch im Windows-Bau LF
    PKGBUILD.write_text(pkgbuild, newline="\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("show", "set"))
    ap.add_argument("version", nargs="?")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.cmd == "show":
        print(dev_version())
    else:
        set_version(args.version or dev_version(), args.apply)


if __name__ == "__main__":
    main()
