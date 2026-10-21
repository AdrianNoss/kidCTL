#!/usr/bin/env python3
"""Debian-/Ubuntu-Pakete bauen – ohne dpkg, nur mit der Python-Standardbibliothek (Bau auf Arch).

    packaging/debian/build-deb.py <ausgabe-ordner>

Baut ``kidctl_<ver>_amd64.deb`` sowie ``kidctl-gnome`` und ``kidctl-plasma`` (``all``). Version aus
``packaging/arch/PKGBUILD`` (pkgver-pkgrel). Mitgebrachte Bibliotheken für Python 3.12 (``vendor.txt``) werden
einmal nach ``~/.cache/kidctl-vendor`` geladen und gegen ihre SHA256-Prüfsumme geprüft.

Aufbau einer .deb: ar-Archiv mit ``debian-binary``, ``control.tar.xz`` und ``data.tar.xz``. Alle Dateien
gehören root:root; Zeitstempel = letzter Commit (reproduzierbar).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "packaging" / "debian"
ARCH = ROOT / "packaging" / "arch"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "kidctl-vendor"
VENDOR_PY = "py3.12"
MAINTAINER = "KidCTL <kidctl@localhost>"
HOMEPAGE = "https://github.com/AdrianNoss/kidCTL"
EXCLUDE = re.compile(r"(__pycache__|\.pyc$|/tests?/)")


def version() -> str:
    text = (ARCH / "PKGBUILD").read_text()
    ver = re.search(r"^pkgver=(\S+)", text, re.M).group(1)
    rel = re.search(r"^pkgrel=(\S+)", text, re.M).group(1)
    return f"{ver}-{rel}"


def mtime() -> int:
    try:
        return int(subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"], capture_output=True,
                                  text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return int(time.time())


class Tree:
    """Dateien eines Pakets: Pfad im System -> (Inhalt, Modus)."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, int]] = {}
        self.links: dict[str, str] = {}

    def add(self, path: str, data: bytes | str, mode: int = 0o644) -> None:
        self.files[path] = (data.encode() if isinstance(data, str) else data, mode)

    def add_file(self, path: str, src: Path, mode: int | None = None) -> None:
        self.add(path, src.read_bytes(), mode if mode is not None else (0o755 if os.access(src, os.X_OK) else 0o644))

    def add_tree(self, prefix: str, src: Path) -> None:
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(src).as_posix()
            if f.is_file() and not EXCLUDE.search("/" + rel):
                self.add(f"{prefix}/{rel}", f.read_bytes(), 0o644)

    def link(self, path: str, target: str) -> None:
        self.links[path] = target

    def tar(self, ts: int) -> bytes:
        dirs = set()
        for p in list(self.files) + list(self.links):
            parts = p.strip("/").split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz", format=tarfile.GNU_FORMAT) as tar:
            def info(name: str, kind, mode: int, size: int = 0) -> tarfile.TarInfo:
                ti = tarfile.TarInfo("./" + name)
                ti.type, ti.mode, ti.size, ti.mtime = kind, mode, size, ts
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = "root"
                return ti
            for d in sorted(dirs):
                tar.addfile(info(d, tarfile.DIRTYPE, 0o755))
            for p, (data, mode) in sorted(self.files.items()):
                tar.addfile(info(p.strip("/"), tarfile.REGTYPE, mode, len(data)), io.BytesIO(data))
            for p, target in sorted(self.links.items()):
                ti = info(p.strip("/"), tarfile.SYMTYPE, 0o777)
                ti.linkname = target
                tar.addfile(ti)
        return buf.getvalue()

    def installed_size(self) -> int:
        return (sum(len(d) for d, _m in self.files.values()) + 1023) // 1024


def ar(members: list[tuple[str, bytes]], ts: int) -> bytes:
    out = io.BytesIO()
    out.write(b"!<arch>\n")
    for name, data in members:
        out.write(f"{name + '/':<16}{ts:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode())
        out.write(data)
        if len(data) % 2:
            out.write(b"\n")
    return out.getvalue()


def build_deb(out_dir: Path, name: str, arch: str, fields: dict[str, str], tree: Tree,
              scripts: dict[str, str] | None = None, conffiles: list[str] = (), ts: int = 0) -> Path:
    ver = fields["Version"]
    control = {"Package": name, **fields, "Architecture": arch, "Maintainer": MAINTAINER,
               "Installed-Size": str(tree.installed_size()), "Homepage": HOMEPAGE}
    text = "".join(f"{k}: {v}\n" for k, v in control.items() if v)
    ctrl = Tree()
    ctrl.add("control", text)
    for script, body in (scripts or {}).items():
        ctrl.add(script, body, 0o755)
    if conffiles:
        ctrl.add("conffiles", "".join(f"{c}\n" for c in conffiles))
    md5 = "".join(f"{hashlib.md5(d).hexdigest()}  {p.strip('/')}\n" for p, (d, _m) in sorted(tree.files.items()))
    ctrl.add("md5sums", md5)
    deb = out_dir / f"{name}_{ver}_{arch}.deb"
    deb.write_bytes(ar([("debian-binary", b"2.0\n"), ("control.tar.xz", ctrl.tar(ts)),
                        ("data.tar.xz", tree.tar(ts))], ts))
    return deb


# ---------- Mitgebrachte Bibliotheken ----------

def vendor_wheels() -> list[Path]:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = []
    for line in (HERE / "vendor.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fname, sha, url = line.split()
        path = CACHE / fname
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            print(f"lade {fname}")
            data = urllib.request.urlopen(url, timeout=60).read()
            if hashlib.sha256(data).hexdigest() != sha:
                sys.exit(f"Prüfsumme falsch: {fname}")
            path.write_bytes(data)
        out.append(path)
    return out


def add_vendor(tree: Tree, prefix: str) -> None:
    """Wheels auspacken (reine Dateikopie, wie pip ohne Skripte)."""
    for whl in vendor_wheels():
        with zipfile.ZipFile(whl) as z:
            for n in sorted(z.namelist()):
                if not n.endswith("/"):
                    tree.add(f"{prefix}/{n}", z.read(n), 0o644)


# ---------- Pakete ----------

def kidctl_tree() -> Tree:
    t = Tree()
    lib = "/usr/lib/kidctl"
    for pkg in ("agent/kidctl_agent", "common/kidctl_common", "tray/kidctl_tray"):
        t.add_tree(f"{lib}/{Path(pkg).name}", ROOT / pkg)
    for script in ("kidctl-main.py", "kidctl-tray-main.py"):
        t.add_file(f"{lib}/{script}", ARCH / script, 0o644)
    t.add_file(f"{lib}/base-programs.txt", HERE / "base-programs.txt", 0o644)
    t.add_file(f"{lib}/kidctl-noexec.sh", ROOT / "packaging/mounts/kidctl-noexec.sh", 0o755)
    t.add_file(f"{lib}/kidctl-nsswitch.sh", ROOT / "packaging/dns/kidctl-nsswitch.sh", 0o755)
    add_vendor(t, f"{lib}/vendor/{VENDOR_PY}")
    t.add_file("/usr/bin/kidctl", ARCH / "kidctl", 0o755)
    t.add_file("/usr/bin/kidctl-tray", ARCH / "kidctl-tray", 0o755)
    systemd = "/usr/lib/systemd/system"
    t.add_file(f"{systemd}/kidctl-agent.service", ARCH / "kidctl-agent.service", 0o644)
    t.add_file(f"{systemd}/kidctl-noexec.service", ROOT / "packaging/mounts/kidctl-noexec.service", 0o644)
    dns = (ROOT / "packaging/dns/kidctl-dns@.service").read_text().replace("/usr/bin/unbound", "/usr/sbin/unbound")
    t.add(f"{systemd}/kidctl-dns@.service", dns)
    # Priorität 200 > sss (128): dessen „sufficient pam_localuser“ beendete die Prüfung für lokale User sonst
    # vor der Login-Sperre (Ubuntu 24.04)
    t.add_file("/usr/share/pam-configs/kidctl", HERE / "kidctl.pam-config", 0o644)
    t.add_file("/etc/firefox/policies/policies.json", ROOT / "packaging/dns/firefox-policies.json", 0o644)
    chromium = (ROOT / "packaging/dns/chromium-policies.json").read_bytes()
    t.add("/etc/chromium/policies/managed/kidctl.json", chromium)
    t.add("/etc/opt/chrome/policies/managed/kidctl.json", chromium)
    t.add_file("/usr/share/applications/kidctl-tray.desktop", ARCH / "kidctl-tray.desktop", 0o644)
    t.add_file("/etc/xdg/autostart/kidctl-tray.desktop", ARCH / "kidctl-tray.desktop", 0o644)
    t.add_file("/usr/share/icons/hicolor/scalable/apps/kidctl.svg", ARCH / "kidctl.svg", 0o644)
    t.add_file("/usr/share/doc/kidctl/copyright", ROOT / "LICENSE", 0o644)
    return t


KIDCTL_CONFFILES = ["/etc/firefox/policies/policies.json", "/etc/chromium/policies/managed/kidctl.json",
                    "/etc/opt/chrome/policies/managed/kidctl.json", "/etc/xdg/autostart/kidctl-tray.desktop"]


def build(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ver, ts = version(), mtime()
    debs = []
    scripts = {s: (HERE / s).read_text() for s in ("postinst", "prerm", "postrm")}
    debs.append(build_deb(out_dir, "kidctl", "amd64", {
        "Version": ver, "Section": "admin", "Priority": "optional",
        "Depends": ", ".join([
            "python3 (>= 3.12)", "python3-dbus-next",
            # Ubuntu 24.04 (Python 3.12): pydantic 2 und websockets bringt das Paket mit
            "python3-pydantic (>= 2) | python3 (<< 3.13)", "python3-websockets (>= 13) | python3 (<< 3.13)",
            "python3-pyside6.qtwidgets | python3-pyqt6", "python3-pyside6.qtnetwork | python3-pyqt6",
            "libnotify-bin", "util-linux", "systemd", "libpam-modules", "libpam-runtime", "unbound", "nftables"]),
        "Recommends": "kidctl-gnome | kidctl-plasma",
        "Description": "Parental Control – Agent (Bildschirmzeit, Login-Sperre) und Tray-App\n"
                       " Root-Agent und Tray-App von KidCTL. Desktop-Anpassungen in kidctl-gnome bzw. kidctl-plasma.",
    }, kidctl_tree(), scripts, KIDCTL_CONFFILES, ts))

    gnome = Tree()
    gnome.add("/usr/share/kidctl/desktop/gnome", "")
    gnome.add_file("/usr/share/glib-2.0/schemas/90_kidctl.gschema.override", HERE / "90_kidctl.gschema.override")
    gnome.add_file("/usr/share/applications/kidctl.desktop", ROOT / "packaging/gnome/kidctl.desktop")
    debs.append(build_deb(out_dir, "kidctl-gnome", "all", {
        "Version": ver, "Section": "admin", "Priority": "optional",
        "Depends": f"kidctl (= {ver}), gnome-shell, gnome-shell-extension-appindicator",
        "Description": "Parental Control – Anpassungen für GNOME\n"
                       " Tray-Symbol per AppIndicator-Erweiterung und Eintrag „KidCTL“ in der Anwendungsübersicht.",
    }, gnome, {"postinst": GLIB_SCHEMAS, "postrm": GLIB_SCHEMAS}, (), ts))

    plasma = Tree()
    plasma.add("/usr/share/kidctl/desktop/plasma", "")
    theme = "/usr/share/sddm/themes/kidctl"
    for f in ("Main.qml", "metadata.desktop"):
        plasma.add_file(f"{theme}/{f}", ROOT / "packaging/sddm/theme" / f, 0o644)
    plasma.link(f"{theme}/theme.conf", "/usr/share/sddm/themes/breeze/theme.conf")
    plasma.add_file("/etc/sddm.conf.d/zz-kidctl-theme.conf", ROOT / "packaging/sddm/zz-kidctl-theme.conf", 0o644)
    debs.append(build_deb(out_dir, "kidctl-plasma", "all", {
        "Version": ver, "Section": "admin", "Priority": "optional",
        "Depends": f"kidctl (= {ver}), sddm, sddm-theme-breeze",
        "Description": "Parental Control – Anpassungen für KDE Plasma\n"
                       " SDDM-Theme mit dem Hinweis der Login-Sperre.",
    }, plasma, None, ["/etc/sddm.conf.d/zz-kidctl-theme.conf"], ts))
    return debs


GLIB_SCHEMAS = """#!/bin/sh
set -e
# AppIndicator-Voreinstellung aktivieren bzw. entfernen
if [ -x /usr/bin/glib-compile-schemas ]; then glib-compile-schemas /usr/share/glib-2.0/schemas || true; fi
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for deb in build(Path(sys.argv[1])):
        print(deb)
