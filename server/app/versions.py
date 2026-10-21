"""Neueste Agent-Version im Pacman-Repository (``<data>/repo/kidctl-<ver>-<rel>-any.pkg.tar.zst``) und
Vergleich mit der Version, die ein Gerät beim Verbinden meldet."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

PKG = re.compile(r"^kidctl-(\d+(?:\.\d+)*)-(\d+)-any\.pkg\.tar\.(?:zst|xz)$")
_cache: tuple[float, "Release | None"] | None = None


@dataclass(frozen=True)
class Release:
    version: str
    published: datetime   # UTC ohne Zeitzone (wie die DB)


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version))


def latest(repo: Path | None = None) -> Release | None:
    """Neueste Version im Repository (zwischengespeichert, bis sich das Verzeichnis ändert)."""
    global _cache
    repo = repo or Path(settings.data_dir) / "repo"
    try:
        mtime = repo.stat().st_mtime
    except OSError:
        return None
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    best = None
    for f in repo.iterdir():
        m = PKG.match(f.name)
        if m and (best is None or _key(m.group(1)) > _key(best[0])):
            best = (m.group(1), f.stat().st_mtime)
    release = Release(best[0], datetime.fromtimestamp(best[1], timezone.utc).replace(tzinfo=None)) if best else None
    _cache = (mtime, release)
    return release


def update_for(agent_version: str, release: Release | None = None) -> str | None:
    """Neuere Version, falls der Agent veraltet ist (sonst/unbekannt: ``None``)."""
    release = release or latest()
    if release is None or not agent_version or not re.match(r"^\d", agent_version):
        return None
    return release.version if _key(agent_version) < _key(release.version) else None
