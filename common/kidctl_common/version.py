"""Versionsnummern von kidCTL: Releases (``0.48.0``) und Vorab-Versionen der Testinstanz (``0.48.0dev312``).

Eine Vorab-Version ist kleiner als das Release derselben Nummer und größer als jedes ältere Release – so sortieren
auch pacman (``vercmp``: Buchstaben direkt hinter der Zahl) und dpkg (``0.48.0~dev312``, Schreibweise im APT-Repo).
Vorab-Versionen entstehen nur im Bau aus dem Branch ``dev`` (``packaging/devversion.py``), nie im Repo.
"""

from __future__ import annotations

import re

PATTERN = r"\d+(?:\.\d+)*(?:dev\d+)?"     # Agent, pacman, Windows-Installer (ohne „~“)
_PARSE = re.compile(r"^(\d+(?:\.\d+)*)(?:[.~]?dev(\d+))?$")


def version_key(version: str) -> tuple:
    """Sortierschlüssel; unbekannte Formate wie bisher nur über ihre Ziffern."""
    m = _PARSE.match((version or "").strip())
    if not m:
        return tuple(int(x) for x in re.findall(r"\d+", version or "")), 1, 0
    nums = tuple(int(x) for x in m.group(1).split("."))
    return (nums, 0, int(m.group(2))) if m.group(2) else (nums, 1, 0)


def is_prerelease(version: str) -> bool:
    m = _PARSE.match(version or "")
    return bool(m and m.group(2))


def normalize(version: str) -> str:
    """APT-Schreibweise ``0.48.0~dev3`` → ``0.48.0dev3``."""
    return version.replace("~dev", "dev")


def deb_version(version: str) -> str:
    """``0.48.0dev3`` → ``0.48.0~dev3`` (dpkg sortiert „~“ vor das Release)."""
    return re.sub(r"\.?dev(?=\d+$)", "~dev", version) if is_prerelease(version) else version


def next_dev(base: str, counter: int) -> str:
    """Vorab-Version zum nächsten Minor-Release nach ``base``: ``0.47.2``, 312 → ``0.48.0dev312``."""
    major, minor = (int(x) for x in base.split(".")[:2])
    return f"{major}.{minor + 1}.0dev{counter}"


def file_version(version: str) -> str:
    """Numerische Windows-Dateiversion (4 Teile, je ≤ 65535) in derselben Reihenfolge:
    ``0.48.0`` → ``0.48.0.0``, ``0.48.0dev312`` → ``0.47.65535.312`` (zwischen 0.47.x und 0.48.0)."""
    m = _PARSE.match(version)
    if not m:
        raise ValueError(f"Version {version!r} unbekannt")
    nums = [int(x) for x in m.group(1).split(".")][:3]
    nums += [0] * (3 - len(nums))
    if not m.group(2):
        return ".".join(map(str, [*nums, 0]))
    # Vorgänger der Release-Nummer (letzte Stelle ≠ 0 um eins verringern, dahinter 65535) + Zähler
    i = max((k for k, n in enumerate(nums) if n), default=None)
    if i is None:
        raise ValueError(f"Vorab-Version {version!r} ohne Vorgänger")
    if int(m.group(2)) > 65535:
        raise ValueError(f"Zähler in {version!r} zu groß für eine Dateiversion")
    prev = nums[:i] + [nums[i] - 1] + [65535] * (2 - i)
    return ".".join(map(str, [*prev, int(m.group(2))]))
