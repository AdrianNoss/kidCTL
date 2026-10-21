#!/usr/bin/env python3
"""Signiertes APT-Repository aus .deb-Dateien erzeugen (ohne dpkg/apt-ftparchive, Bau auf Arch).

    packaging/debian/apt-repo.py <debs-ordner> <repo-ordner> --key <fingerabdruck>

Ergebnis (wird ersetzt):
    <repo>/pool/main/<paket>.deb
    <repo>/dists/stable/main/binary-amd64/Packages(.gz)
    <repo>/dists/stable/Release, InRelease (clearsign), Release.gpg (abgetrennt)
Signiert mit GnuPG aus ``$GNUPGHOME`` (wie das Pacman-Repo, siehe packaging/repo/common.sh).
Einbinden auf dem Kinderrechner: packaging/repo/kidctl-repo-client.sh.
"""

from __future__ import annotations

import email.utils
import gzip
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SUITE, COMPONENT, ARCHES = "stable", "main", ("amd64",)


def deb_control(deb: Path) -> str:
    """control-Datei aus einer .deb (ar-Archiv mit control.tar.*)."""
    data = deb.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError(f"{deb}: keine .deb")
    pos = 8
    while pos < len(data):
        name = data[pos:pos + 16].decode().strip().rstrip("/")
        size = int(data[pos + 48:pos + 58].decode().strip())
        body = data[pos + 60:pos + 60 + size]
        if name.startswith("control.tar"):
            with tarfile.open(fileobj=io.BytesIO(body)) as tar:
                return tar.extractfile("./control").read().decode()
        pos += 60 + size + (size % 2)
    raise ValueError(f"{deb}: keine control-Datei")


def packages_entry(deb: Path, rel: str) -> str:
    data = deb.read_bytes()
    control = deb_control(deb).rstrip("\n")
    return (f"{control}\nFilename: {rel}\nSize: {len(data)}\nMD5sum: {hashlib.md5(data).hexdigest()}\n"
            f"SHA256: {hashlib.sha256(data).hexdigest()}\n")


def build(debs_dir: Path, repo: Path, key: str) -> None:
    debs = sorted(debs_dir.glob("*.deb"))
    if not debs:
        sys.exit(f"keine .deb in {debs_dir}")
    for d in ("pool", "dists"):
        shutil.rmtree(repo / d, ignore_errors=True)
    pool = repo / "pool" / COMPONENT
    pool.mkdir(parents=True)
    entries = []
    for deb in debs:
        shutil.copy2(deb, pool / deb.name)
        entries.append(packages_entry(pool / deb.name, f"pool/{COMPONENT}/{deb.name}"))
    dist = repo / "dists" / SUITE
    index_files = []
    for arch in ARCHES:
        d = dist / COMPONENT / f"binary-{arch}"
        d.mkdir(parents=True)
        text = "\n".join(entries).encode()
        (d / "Packages").write_bytes(text)
        (d / "Packages.gz").write_bytes(gzip.compress(text, mtime=0))
        index_files += [d / "Packages", d / "Packages.gz"]
    lines = [f"Origin: KidCTL", f"Label: KidCTL", f"Suite: {SUITE}", f"Codename: {SUITE}",
             f"Date: {email.utils.formatdate(usegmt=True)}", f"Architectures: {' '.join(ARCHES)}",
             f"Components: {COMPONENT}", "Description: KidCTL Parental Control", "SHA256:"]
    for f in index_files:
        data = f.read_bytes()
        lines.append(f" {hashlib.sha256(data).hexdigest()} {len(data):>8} {f.relative_to(dist).as_posix()}")
    release = dist / "Release"
    release.write_text("\n".join(lines) + "\n")
    gpg = ["gpg", "--batch", "--yes", "--local-user", key, "--digest-algo", "SHA512"]
    subprocess.run(gpg + ["--clearsign", "-o", str(dist / "InRelease"), str(release)], check=True)
    subprocess.run(gpg + ["--armor", "--detach-sign", "-o", str(dist / "Release.gpg"), str(release)], check=True)
    print(f"APT-Repo: {len(debs)} Pakete in {repo}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) != 4 or args[2] != "--key":
        sys.exit(__doc__)
    build(Path(args[0]), Path(args[1]), args[3])
