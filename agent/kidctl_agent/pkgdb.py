"""Paketdatenbank des Systems für die Basisliste: pacman (Arch) oder dpkg (Debian, Ubuntu, Mint, Pop!_OS).

Einheitliche Sicht:
- ``packages()``: installierte Pakete -> Abhängigkeiten, bereitgestellte Namen, Paketgruppen.
  dpkg: Pre-Depends, Depends **und Recommends** (Metapakete wie ``ubuntu-desktop`` ziehen den Großteil des
  Desktops über Empfehlungen nach); bei Alternativen (``a | b``) zählen alle installierten. Paketgruppen
  gibt es dort nicht – stattdessen die Pseudogruppe ``essential`` (Pakete mit ``Essential: yes`` wie grep, sed,
  coreutils: von nichts abhängig gemacht, aber immer vorhanden).
- ``files(pakete)``: Dateien dieser Pakete (dpkg: aus ``/var/lib/dpkg/info/<paket>.list``; Pfade unter
  ``/bin``, ``/sbin``, ``/lib`` werden auf ``/usr/…`` abgebildet, wenn das System zusammengeführt ist).
- ``listing()``: „<paket> <pfad>“ je Datei, Verzeichnisse mit „/“ am Ende (wie ``pacman -Ql``).
- ``db_path``: ändert sich nach jeder Installation (Änderungszeit = Basisliste neu berechnen).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/sbin"}
MERGED = ("/bin", "/sbin", "/lib", "/lib64", "/lib32", "/libx32")


def _run(*cmd: str, timeout: int = 60) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, check=True, timeout=timeout).stdout


# ---------- pacman ----------

def _qi_blocks(text: str):
    """``pacman -Qi`` (LC_ALL=C) -> Felder je Paket (Fortsetzungszeilen angehängt)."""
    for block in text.strip().split("\n\n"):
        fields: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            m = re.match(r"^(\S[^:]*?)\s*: (.*)$", line)
            if m:
                key = m.group(1)
                fields[key] = m.group(2)
            elif key:
                fields[key] += " " + line.strip()
        if fields.get("Name"):
            yield fields


def _names(v: str) -> list[str]:
    return [re.split(r"[<>=]", x)[0] for x in v.split()] if v and v != "None" else []


def parse_pacman_qi(text: str) -> tuple[dict[str, list[str]], dict[str, str], dict[str, set[str]]]:
    """``pacman -Qi`` -> (Paket -> Abhängigkeiten, bereitgestellter Name -> Paket, Paket -> Gruppen)."""
    deps: dict[str, list[str]] = {}
    provides: dict[str, str] = {}
    groups: dict[str, set[str]] = {}
    for fields in _qi_blocks(text):
        name = fields["Name"]
        deps[name] = _names(fields.get("Depends On", ""))
        groups[name] = set(_names(fields.get("Groups", "")))
        for prov in _names(fields.get("Provides", "")):
            provides.setdefault(prov, name)
    return deps, provides, groups


class Pacman:
    name = "pacman"
    db_path = "/var/lib/pacman/local"

    def packages(self):
        return parse_pacman_qi(_run("pacman", "-Qi"))

    def files(self, pkgs: list[str]) -> list[str]:
        return _run("pacman", "-Qlq", *pkgs).split() if pkgs else []

    def listing(self) -> str:
        return _run("pacman", "-Ql", timeout=120)


# ---------- dpkg ----------

DPKG_FORMAT = (r"${db:Status-Abbrev}\t${Package}\t${Pre-Depends}\t${Depends}\t${Recommends}\t${Provides}"
               r"\t${Essential}\n")


def _dep_names(field: str) -> list[str]:
    """``a (>= 1), b:any | c`` -> [a, b, c] (Versionen, Architektur-Zusätze entfernt)."""
    out = []
    for part in re.split(r"[,|]", field):
        name = part.strip().split(" ")[0].split("(")[0].split(":")[0].strip()
        if name:
            out.append(name)
    return out


def parse_dpkg_query(text: str) -> tuple[dict[str, list[str]], dict[str, str], dict[str, set[str]]]:
    """Ausgabe von ``dpkg-query -W -f DPKG_FORMAT`` -> wie ``parse_pacman_qi`` (nur installierte Pakete)."""
    deps: dict[str, list[str]] = {}
    provides: dict[str, str] = {}
    groups: dict[str, set[str]] = {}
    for line in text.splitlines():
        cols = line.split("\t")
        # installiert: Soll i/h, Ist i oder mit ausstehendem Trigger (t/W – z. B. während eines apt-Laufs,
        # in dem der Agent neu startet; sonst fehlten etwa die GLib-Programme bis zur nächsten Berechnung)
        if len(cols) < 6 or len(cols[0]) < 2 or cols[0][0] not in "ih" or cols[0][1] not in "itW":
            continue
        name = cols[1].split(":")[0]
        if len(cols) > 6 and cols[6].strip() == "yes":
            groups[name] = {"essential"}
        deps.setdefault(name, [])
        deps[name] += [d for d in _dep_names(cols[2]) + _dep_names(cols[3]) + _dep_names(cols[4])
                       if d not in deps[name]]
        for prov in _dep_names(cols[5]):
            provides.setdefault(prov, name)
    return deps, provides, groups


def merged_dirs(root: str = "/") -> dict[str, str]:
    """Zusammengeführte Verzeichnisse (usrmerge): ``/bin`` -> ``/usr/bin``, falls ``/bin`` ein Verweis ist."""
    out = {}
    for d in MERGED:
        p = os.path.join(root, d.lstrip("/"))
        if os.path.islink(p):
            out[d] = "/usr" + d
    return out


def normalize(path: str, merged: dict[str, str]) -> str:
    for old, new in merged.items():
        if path == old or path.startswith(old + "/"):
            return new + path[len(old):]
    return path


class Dpkg:
    name = "dpkg"
    db_path = "/var/lib/dpkg/status"

    def __init__(self, info_dir: str = "/var/lib/dpkg/info", root: str = "/") -> None:
        self.info_dir = Path(info_dir)
        self.merged = merged_dirs(root)

    def packages(self):
        return parse_dpkg_query(_run("dpkg-query", "-W", "-f", DPKG_FORMAT))

    def _lists(self) -> dict[str, list[Path]]:
        """Paket -> seine ``.list``-Dateien (``name.list`` bzw. ``name:arch.list``)."""
        out: dict[str, list[Path]] = {}
        for f in self.info_dir.glob("*.list"):
            out.setdefault(f.stem.split(":")[0], []).append(f)
        return out

    def _paths(self, files: list[Path]) -> list[str]:
        out = []
        for f in files:
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            out += [normalize(p, self.merged) for p in text.splitlines() if p.startswith("/")]
        return out

    def files(self, pkgs: list[str]) -> list[str]:
        lists = self._lists()
        return self._paths([f for p in pkgs for f in lists.get(p, [])])

    def listing(self) -> str:
        per_pkg = {pkg: self._paths(files) for pkg, files in self._lists().items()}
        parents = {os.path.dirname(p) for paths in per_pkg.values() for p in paths}
        lines = []
        for pkg, paths in sorted(per_pkg.items()):
            for p in paths:
                lines.append(f"{pkg} {p}/" if p in parents else f"{pkg} {p}")
        return "\n".join(lines)


def detect():
    """Paketverwaltung dieses Systems (``None``, wenn keine bekannte gefunden)."""
    if os.path.isdir(Pacman.db_path):
        return Pacman()
    if os.path.isfile(Dpkg.db_path):
        return Dpkg()
    return None
