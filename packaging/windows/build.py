#!/usr/bin/env python3
"""Windows-Paket bauen: Staging auf dem Linux-Rechner, Installer mit Inno Setup in der Windows-VM, Signatur.

    build.py keygen  [--apply]   Ed25519-Schlüssel für Updates anlegen (einmalig)
    build.py stage   [--apply]   eingebettetes Python + Bibliotheken (vendor.lock) + kidctl-Code zusammenstellen
    build.py compile [--apply]   Installer in der Windows-VM bauen (dev/vm/winvm.sh, Inno Setup), Ergebnis ins Repo
    build.py sign    [--apply]   latest.json + Signatur für den neuesten Installer schreiben, ältere bis auf einen löschen
    build.py all     [--apply]   stage + compile + sign
    build.py ci      [--apply]   unter Windows (GitHub Actions): Inno Setup bei Bedarf installieren, stage, Installer
                                 nach --out bauen – ohne geheimen Schlüssel, für die Signatur über SignPath
    build.py fetch   [--apply]   signierten Installer des letzten (oder --run) GitHub-Laufs ins Repo holen;
                                 danach „sign --apply“ für latest.json

Ohne --apply nur Anzeige (Dry-Run). Veröffentlichen wie die übrigen Pakete mit
``packaging/repo/kidctl-repo-publish.sh --apply`` (Ordner ``windows/`` im Repo, Server: ``/repo/windows/``).

Orte: Repo ``$KIDCTL_REPO_HOME/repo`` (Standard ``~/.local/share/kidctl-repo/repo``), Schlüssel
``$KIDCTL_REPO_HOME/windows-update.key`` (0600), Zwischenspeicher ``~/.cache/kidctl-windows``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "common"))
from kidctl_common import ed25519  # noqa: E402

REPO_HOME = Path(os.environ.get("KIDCTL_REPO_HOME", Path.home() / ".local/share/kidctl-repo"))
REPO_WIN = REPO_HOME / "repo" / "windows"
KEY = REPO_HOME / "windows-update.key"
UPDATE_PUB = HERE / "update-key.pub"   # öffentlicher Teil im Repo, damit auch der CI-Bau ihn einbettet
GITHUB_REPO = "AdrianNoss/kidCTL"
SIGNED_ARTIFACT = "KidCTL-Setup-signed"
CACHE = Path(os.environ.get("KIDCTL_WIN_CACHE", Path.home() / ".cache/kidctl-windows"))
STAGING = CACHE / "staging"
WINVM = ROOT / "dev" / "vm" / "winvm.sh"
VM_BUILD = "C:/kidctl-build"
ISCC = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
PACKAGES = (("agent", "kidctl_agent"), ("common", "kidctl_common"), ("tray", "kidctl_tray"))

# PySide6 verkleinern: nur QtCore/Gui/Widgets/Network (+ Svg für Symbole) werden gebraucht
QT_KEEP_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg"}
QT_KEEP_DLLS = {"Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Network", "Qt6Svg", "Qt6OpenGL"}
QT_DROP_DIRS = ("qml", "include", "typesystems", "glue", "scripts", "metatypes", "resources", "doc")

APPLY = False
UNBOUND: tuple[str, str] | None = None
INNOSETUP: tuple[str, str] | None = None


def say(msg: str) -> None:
    print(f"==> {msg}")


def version() -> str:
    text = (ROOT / "agent" / "kidctl_agent" / "__init__.py").read_text()
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lock() -> tuple[tuple[str, str], list[tuple[str, str, str, str]]]:
    python, wheels = None, []
    global UNBOUND, INNOSETUP
    for line in (HERE / "vendor.lock").read_text().splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "python":
            python = (parts[1], parts[2])
        elif parts[0] == "unbound":
            UNBOUND = (parts[1], parts[2])
        elif parts[0] == "innosetup":
            INNOSETUP = (parts[1], parts[2])
        elif parts[0] == "wheel":
            wheels.append(tuple(parts[1:5]))
    if python is None:
        sys.exit("vendor.lock: Zeile „python“ fehlt")
    return python, wheels


def fetch(url: str, name: str, digest: str) -> Path:
    """Datei in den Zwischenspeicher laden (falls nötig) und gegen die Prüfsumme prüfen."""
    path = CACHE / "downloads" / name
    if not path.exists():
        if not APPLY:
            print(f"[dry-run] laden {url}")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(path)
    if APPLY and sha256(path) != digest:
        path.unlink()
        sys.exit(f"Prüfsumme falsch: {name} (Datei gelöscht)")
    return path


def wheel_url(project: str, ver: str, filename: str) -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/{ver}/json", timeout=60) as r:
        data = json.load(r)
    for f in data["urls"]:
        if f["filename"] == filename:
            return f["url"]
    sys.exit(f"{filename} nicht auf PyPI ({project} {ver})")


def prune_qt(site: Path) -> None:
    qt = site / "PySide6"
    for d in QT_DROP_DIRS:
        shutil.rmtree(qt / d, ignore_errors=True)
    for f in list(qt.iterdir()):
        stem = f.name.split(".")[0]
        if f.suffix == ".exe":
            f.unlink()
        elif f.suffix in (".pyd", ".pyi") and stem.startswith("Qt") and stem not in QT_KEEP_MODULES:
            f.unlink()
        elif f.suffix == ".dll" and stem.startswith("Qt6") and stem not in QT_KEEP_DLLS:
            f.unlink()
    trans = qt / "translations"
    if trans.is_dir():
        for f in trans.iterdir():
            if not re.match(r"qtbase_(de|en)\.qm$", f.name):
                f.unlink() if f.is_file() else shutil.rmtree(f)


def cmd_keygen() -> None:
    if KEY.exists():
        say(f"Schlüssel vorhanden: {KEY}")
        return
    if not APPLY:
        print(f"[dry-run] Ed25519-Schlüssel anlegen: {KEY} (0600) und {KEY.with_suffix('.pub')}")
        return
    KEY.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret.hex() + "\n")
    KEY.with_suffix(".pub").write_text(ed25519.public_key(secret).hex() + "\n")
    say(f"Schlüssel angelegt: {KEY} – sicher aufbewahren (ohne ihn keine Updates für installierte Rechner)")


def update_pub() -> str:
    """Öffentlicher Update-Schlüssel: aus dem Repo, abgeglichen mit dem geheimen Schlüssel, falls vorhanden."""
    pub = UPDATE_PUB.read_text().strip() if UPDATE_PUB.exists() else ""
    if KEY.exists():
        own = ed25519.public_key(secret_key()).hex()
        if pub and pub != own:
            sys.exit(f"{UPDATE_PUB} passt nicht zum geheimen Schlüssel {KEY}")
        pub = own
    return pub


def secret_key() -> bytes:
    if not KEY.exists():
        sys.exit(f"Kein Update-Schlüssel ({KEY}) – erst „build.py keygen --apply“.")
    return bytes.fromhex(KEY.read_text().strip())


def cmd_stage() -> None:
    (py_url, py_sha), wheels = lock()
    ver = version()
    say(f"Staging für KidCTL {ver}: {STAGING}")
    embed = fetch(py_url, py_url.rsplit("/", 1)[1], py_sha)
    if UNBOUND is None:
        sys.exit("vendor.lock: Zeile „unbound“ fehlt")
    unbound_zip = fetch(UNBOUND[0], UNBOUND[0].rsplit("/", 1)[1], UNBOUND[1])
    paths = []
    for project, ver_, fn, sha in wheels:
        cached = (CACHE / "downloads" / fn).exists()
        url = wheel_url(project, ver_, fn) if APPLY and not cached else f"PyPI: {project} {ver_}"
        paths.append((fetch(url, fn, sha), fn))
    pub = update_pub()
    if not pub:
        print("Warnung: kein Update-Schlüssel – der Agent führt keine automatischen Updates aus.")
    if not APPLY:
        print(f"[dry-run] {STAGING}/python (eingebettetes Python + {len(wheels)} Pakete), {STAGING}/lib (kidctl)")
        return
    shutil.rmtree(STAGING, ignore_errors=True)
    py = STAGING / "python"
    site = py / "Lib" / "site-packages"
    site.mkdir(parents=True)
    with zipfile.ZipFile(embed) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    zipname = next(py.glob("python3*.zip")).name
    # Suchpfad: Standardbibliothek, Programmordner, Bibliotheken, kidctl-Code (..\lib); „import site“ für .pth
    pth.write_text(f"{zipname}\n.\nLib\\site-packages\n..\\lib\nimport site\n")
    for path, _fn in paths:
        with zipfile.ZipFile(path) as z:
            z.extractall(site)
    prune_qt(site)
    ub = STAGING / "unbound"
    ub.mkdir()
    with zipfile.ZipFile(unbound_zip) as z:   # nur der Resolver selbst (statisch gelinkt) und die Lizenz
        for name in ("unbound.exe", "LICENSE"):
            (ub / name).write_bytes(z.read(name))
    lib = STAGING / "lib"
    for src, pkg in PACKAGES:
        shutil.copytree(ROOT / src / pkg, lib / pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    (lib / "kidctl_agent" / "win" / "update-key.pub").write_text(pub + "\n" if pub else "")
    shutil.copy2(ROOT / "LICENSE", STAGING / "LICENSE.txt")
    (STAGING / "kidctl.cmd").write_text('@"%~dp0python\\python.exe" -m kidctl_agent %*\r\n')
    size = sum(f.stat().st_size for f in STAGING.rglob("*") if f.is_file())
    say(f"Staging fertig: {size / 1e6:.0f} MB")


def winvm(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(WINVM), *args], check=check)


def iscc_args(ver: str, staging: str, out: str) -> list[str]:
    return ["/Q", f"/DAppVersion={ver}", f"/DStaging={staging}", f"/DOutputDir={out}", "kidctl.iss"]


def cmd_ci(out: Path) -> None:
    """Bau unter Windows ohne VM (GitHub Actions): Ergebnis ist der unsignierte Installer in ``out``."""
    if sys.platform != "win32":
        sys.exit("„ci“ läuft nur unter Windows (GitHub Actions); lokal „all“ mit der VM verwenden.")
    lock()
    iscc = Path(ISCC)
    if not iscc.exists():
        if INNOSETUP is None:
            sys.exit("vendor.lock: Zeile „innosetup“ fehlt")
        setup = fetch(INNOSETUP[0], INNOSETUP[0].rsplit("/", 1)[1], INNOSETUP[1])
        say(f"Inno Setup installieren: {setup.name}")
        if APPLY:
            subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/ALLUSERS"],
                           check=True)
    cmd_stage()
    ver = version()
    say(f"Installer {ver} bauen -> {out}")
    if not APPLY:
        print(f"[dry-run] ISCC {' '.join(iscc_args(ver, str(STAGING), str(out)))}")
        return
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(iscc), *iscc_args(ver, str(STAGING), str(out.resolve()))], cwd=HERE, check=True)
    say(f"Installer: {out / f'KidCTL-Setup-{ver}.exe'}")


def cmd_fetch(run: str | None) -> None:
    """Signierten Installer aus GitHub Actions ins Repo holen (gh CLI, angemeldet)."""
    ver = version()
    target = REPO_WIN / f"KidCTL-Setup-{ver}.exe"
    say(f"Signierten Installer {ver} aus GitHub Actions holen ({GITHUB_REPO}, Lauf {run or 'neuester'})")
    if not APPLY:
        print(f"[dry-run] gh run download {run or '<neuester>'} -n {SIGNED_ARTIFACT} -> {target}")
        return
    if run is None:
        run = subprocess.run(["gh", "run", "list", "-R", GITHUB_REPO, "-w", "windows.yml", "-s", "success", "-L", "1",
                              "--json", "databaseId", "-q", ".[0].databaseId"],
                             check=True, capture_output=True, text=True).stdout.strip()
        if not run:
            sys.exit("Kein erfolgreicher Lauf von windows.yml gefunden.")
    tmp = CACHE / "fetch"
    shutil.rmtree(tmp, ignore_errors=True)
    subprocess.run(["gh", "run", "download", run, "-R", GITHUB_REPO, "-n", SIGNED_ARTIFACT, "-D", str(tmp)], check=True)
    exe = tmp / target.name
    if not exe.exists():
        sys.exit(f"{target.name} fehlt im Artefakt von Lauf {run} – passt die Version ({ver}) zum gebauten Stand?")
    REPO_WIN.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, target)
    say(f"Installer: {target} – jetzt „build.py sign --apply“")


def cmd_compile() -> None:
    ver = version()
    if not (STAGING / "lib").is_dir() and APPLY:
        sys.exit("Kein Staging – erst „build.py stage --apply“.")
    say(f"Installer {ver} in der Windows-VM bauen (Inno Setup)")
    if not APPLY:
        print(f"[dry-run] Staging + kidctl.iss nach {VM_BUILD}, ISCC, KidCTL-Setup-{ver}.exe -> {REPO_WIN}")
        return
    bundle = CACHE / "build.tar"
    with tarfile.open(bundle, "w") as tar:
        tar.add(STAGING, arcname="staging")
        tar.add(HERE / "kidctl.iss", arcname="kidctl.iss")
        tar.add(HERE / "kidctl.ico", arcname="kidctl.ico")
    winvm("ssh", f"Remove-Item -Recurse -Force {VM_BUILD} -ErrorAction SilentlyContinue; "
                 f"New-Item -ItemType Directory {VM_BUILD} | Out-Null")
    winvm("put", str(bundle), f"{VM_BUILD}/build.tar")
    winvm("ssh", f"cd {VM_BUILD}; tar -xf build.tar; "
                 f"& '{ISCC}' {' '.join(iscc_args(ver, f'{VM_BUILD}/staging', f'{VM_BUILD}/out'))}; "
                 f"exit $LASTEXITCODE")
    REPO_WIN.mkdir(parents=True, exist_ok=True)
    winvm("get", f"{VM_BUILD}/out/KidCTL-Setup-{ver}.exe", str(REPO_WIN / f"KidCTL-Setup-{ver}.exe"))
    say(f"Installer: {REPO_WIN / f'KidCTL-Setup-{ver}.exe'}")


def cmd_sign() -> None:
    found = sorted(REPO_WIN.glob("KidCTL-Setup-*.exe"),
                   key=lambda f: tuple(int(x) for x in re.findall(r"\d+", f.stem)))
    if not found:
        if not APPLY:
            print(f"[dry-run] latest.json + latest.json.sig für den neuesten Installer in {REPO_WIN}")
            return
        sys.exit(f"Kein Installer in {REPO_WIN}")
    exe = found[-1]
    ver = re.match(r"KidCTL-Setup-(.+)\.exe$", exe.name).group(1)
    manifest = json.dumps({"version": ver, "file": exe.name, "sha256": sha256(exe)}, sort_keys=True).encode()
    outdated = found[:-2]  # die aktuelle und eine ältere Version bleiben liegen
    say(f"latest.json -> {ver}")
    if not APPLY:
        print(f"[dry-run] {REPO_WIN}/latest.json + .sig")
        for old in outdated:
            print(f"[dry-run] entfernen: {old.name}")
        return
    import base64
    sig = base64.b64encode(ed25519.sign(secret_key(), manifest)).decode()
    (REPO_WIN / "latest.json").write_bytes(manifest)
    (REPO_WIN / "latest.json.sig").write_text(sig + "\n")
    for old in outdated:  # erst nach der neuen Signatur, damit latest.json nie ins Leere zeigt
        old.unlink()
        say(f"Entfernt: {old.name}")


def main() -> None:
    global APPLY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("keygen", "stage", "compile", "sign", "all", "ci", "fetch"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "dist", help="ci: Zielordner des Installers")
    ap.add_argument("--run", help="fetch: Lauf-ID in GitHub Actions (Standard: neuester erfolgreicher)")
    args = ap.parse_args()
    APPLY = args.apply
    if args.cmd == "keygen":
        cmd_keygen()
    if args.cmd in ("stage", "all"):
        cmd_stage()
    if args.cmd in ("compile", "all"):
        cmd_compile()
    if args.cmd in ("sign", "all"):
        cmd_sign()
    if args.cmd == "ci":
        cmd_ci(args.out)
    if args.cmd == "fetch":
        cmd_fetch(args.run)
    if not APPLY:
        print("Dry-Run – mit --apply ausführen.")


if __name__ == "__main__":
    main()
