"""Versionsvergleich mit Vorab-Versionen der Testinstanz (``0.48.0dev312``): kleiner als das Release, wie pacman
(vercmp) und dpkg (``~dev``) sortieren."""

import re
import subprocess
from pathlib import Path

import pytest
from kidctl_common import version as v

ROOT = Path(__file__).resolve().parents[2]


def test_releases_compare_as_before():
    assert v.version_key("0.47.0") < v.version_key("0.47.1") < v.version_key("0.48.0") < v.version_key("0.100.0")
    assert v.version_key("0.29.10") > v.version_key("0.29.9")
    assert v.version_key("1.0") < v.version_key("1.0.0")      # bisheriges Verhalten (Tupelvergleich)


@pytest.mark.parametrize("dev", ["0.48.0dev3", "0.48.0~dev3", "0.48.0.dev3"])
def test_prerelease_sorts_between_releases(dev):
    assert v.version_key("0.47.0") < v.version_key("0.47.9") < v.version_key(dev) < v.version_key("0.48.0")


def test_prerelease_counter():
    assert v.version_key("0.48.0dev3") < v.version_key("0.48.0dev10") < v.version_key("0.48.0dev312")
    assert v.version_key("0.48.0dev3") == v.version_key("0.48.0~dev3")


def test_prerelease_helpers():
    assert v.is_prerelease("0.48.0dev3") and v.is_prerelease("0.48.0~dev3")
    assert not v.is_prerelease("0.48.0")
    assert v.normalize("0.48.0~dev3") == "0.48.0dev3"
    assert v.normalize("0.48.0") == "0.48.0"
    assert v.deb_version("0.48.0dev3") == "0.48.0~dev3"
    assert v.deb_version("0.48.0") == "0.48.0"
    assert v.next_dev("0.47.0", 312) == "0.48.0dev312"
    assert v.next_dev("0.47.2", 5) == "0.48.0dev5"
    assert v.next_dev("1.9.0", 1) == "1.10.0dev1"


def test_file_version_numeric_and_ordered():
    assert v.file_version("0.47.0") == "0.47.0.0"
    assert v.file_version("0.48.0dev312") == "0.47.65535.312"
    assert v.file_version("1.0.0dev2") == "0.65535.65535.2"
    key = lambda s: tuple(int(x) for x in s.split("."))  # noqa: E731 – so vergleicht Windows Dateiversionen
    assert key(v.file_version("0.47.9")) < key(v.file_version("0.48.0dev312")) < key(v.file_version("0.48.0"))
    for s in ("0.47.0", "0.48.0dev312", "1.0.0dev2"):
        assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", v.file_version(s))
        assert all(int(x) <= 65535 for x in v.file_version(s).split("."))


def test_pattern_matches_both_forms():
    assert re.fullmatch(v.PATTERN, "0.48.0") and re.fullmatch(v.PATTERN, "0.48.0dev3")
    assert not re.fullmatch(v.PATTERN, "0.48.0-1") and not re.fullmatch(v.PATTERN, "../x")


@pytest.mark.skipif(not __import__("shutil").which("vercmp"), reason="pacman fehlt")
def test_pacman_agrees():
    for a, b in (("0.48.0dev3", "0.48.0"), ("0.47.0", "0.48.0dev3"), ("0.48.0dev3", "0.48.0dev10")):
        assert subprocess.run(["vercmp", a, b], capture_output=True, text=True).stdout.strip() == "-1", (a, b)


@pytest.mark.skipif(not __import__("shutil").which("dpkg"), reason="dpkg fehlt")
def test_dpkg_agrees():
    for a, b in (("0.48.0~dev3", "0.48.0"), ("0.47.0", "0.48.0~dev3"), ("0.48.0~dev3", "0.48.0~dev10")):
        assert subprocess.run(["dpkg", "--compare-versions", a, "lt", b]).returncode == 0, (a, b)


def _load(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_devversion_set_and_deb(tmp_path, monkeypatch):
    """Bau aus dev: devversion.py trägt die Vorab-Version ein, build-deb.py macht daraus ~dev."""
    dv = _load(ROOT / "packaging" / "devversion.py", "devversion")
    deb = _load(ROOT / "packaging" / "debian" / "build-deb.py", "build_deb")
    init, pkgbuild = tmp_path / "__init__.py", tmp_path / "PKGBUILD"
    init.write_text('"""x"""\n__version__ = "0.47.0"\n')
    pkgbuild.write_text("pkgbase=kidctl\npkgver=0.47.0   # Kommentar\npkgrel=1\n")
    monkeypatch.setattr(dv, "INIT", init)
    monkeypatch.setattr(dv, "PKGBUILD", pkgbuild)
    monkeypatch.setattr(dv, "ROOT", tmp_path)
    dv.set_version("0.48.0dev312", apply=False)
    assert "0.47.0" in init.read_text()                       # Dry-Run ändert nichts
    dv.set_version("0.48.0dev312", apply=True)
    assert '__version__ = "0.48.0dev312"' in init.read_text()
    assert "pkgver=0.48.0dev312   # Kommentar\n" in pkgbuild.read_text()
    with pytest.raises(SystemExit):
        dv.set_version("0.48.0", apply=True)                  # nur Vorab-Versionen
    monkeypatch.setattr(deb, "ARCH", tmp_path)
    assert deb.version() == "0.48.0~dev312-1"


def test_devversion_show_uses_commit_count():
    dv = _load(ROOT / "packaging" / "devversion.py", "devversion")
    count = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD"], capture_output=True,
                           text=True).stdout.strip()
    base = dv.base_version()
    if v.is_prerelease(base):
        pytest.skip("Bau-Checkout mit Vorab-Version")
    assert dv.dev_version() == v.next_dev(base, int(count))


def test_windows_build_prerelease(tmp_path, monkeypatch):
    """Installer: Release-Aufruf unverändert, Vorab-Version mit numerischer Dateiversion; sign sortiert devN richtig;
    --dev schreibt in ein eigenes Repo."""
    b = _load(ROOT / "packaging" / "windows" / "build.py", "winbuild")
    assert b.iscc_args("0.47.0", "S", "O") == ["/Q", "/DAppVersion=0.47.0", "/DStaging=S", "/DOutputDir=O",
                                                "kidctl.iss"]
    assert "/DFileVersion=0.47.65535.312" in b.iscc_args("0.48.0dev312", "S", "O")
    names = ["KidCTL-Setup-0.47.0.exe", "KidCTL-Setup-0.48.0dev99.exe", "KidCTL-Setup-0.48.0dev312.exe"]
    assert b.newest_first(names) == ["KidCTL-Setup-0.48.0dev312.exe", "KidCTL-Setup-0.48.0dev99.exe",
                                     "KidCTL-Setup-0.47.0.exe"]
    assert b.newest_first(["KidCTL-Setup-0.48.0dev312.exe", "KidCTL-Setup-0.48.0.exe"])[0] == "KidCTL-Setup-0.48.0.exe"
    prod = b.REPO_WIN
    b.use_channel("dev")
    assert b.REPO_WIN == prod.parent.parent / "repo-dev" / "windows" and b.WORKFLOW == "prerelease.yml"
    iss = (ROOT / "packaging" / "windows" / "kidctl.iss").read_text()
    assert "VersionInfoVersion={#FileVersion}" in iss and "#define FileVersion AppVersion" in iss


def _publish(tmp_path, *args, env=None):
    import os
    home, bindir = tmp_path / "home", tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "rsync").write_text('#!/bin/sh\necho "rsync $*"\n')
    (bindir / "rsync").chmod(0o755)
    for repo in ("repo", "repo-dev"):
        (home / repo).mkdir(parents=True, exist_ok=True)
        (home / repo / "kidctl.db.tar.zst").write_text("")
    e = {"PATH": f"{bindir}:{os.environ['PATH']}", "KIDCTL_REPO_HOME": str(home), "HOME": str(tmp_path), **(env or {})}
    return subprocess.run(["bash", str(ROOT / "packaging" / "repo" / "kidctl-repo-publish.sh"), *args],
                          capture_output=True, text=True, env=e)


def test_publish_dev_never_hits_prod(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "publish-target").write_text("u@srv02:/opt/kidCTL/data/repo/\n")
    r = _publish(tmp_path)                                                   # Prod unverändert
    assert r.returncode == 0 and f"{tmp_path}/home/repo/ u@srv02:/opt/kidCTL/data/repo/" in r.stdout
    r = _publish(tmp_path, "--dev")                                          # kein dev-Ziel: Abbruch, kein Fallback
    assert r.returncode != 0 and "rsync" not in r.stdout and "KIDCTL_DEV_REPO_TARGET" in r.stderr
    r = _publish(tmp_path, "--dev", env={"KIDCTL_DEV_REPO_TARGET": "u@srv02:/opt/kidCTL/data/repo"})
    assert r.returncode != 0 and "rsync" not in r.stdout                      # Prod-Ziel als dev-Ziel verweigert
    (tmp_path / "home" / "publish-target-dev").write_text("u@srv02:/opt/kidCTL-dev/data/repo/\n")
    r = _publish(tmp_path, "--dev")
    assert r.returncode == 0 and f"{tmp_path}/home/repo-dev/ u@srv02:/opt/kidCTL-dev/data/repo/" in r.stdout
    assert "--dry-run" in r.stdout
