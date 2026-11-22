"""Info zur laufenden Version: Server-Version, Release-Datum, Protokollversion, Projekt-Links, Hinweis „Neu in
Version …“ für Eltern und Prüfung auf eine neuere Version.

Neuere Version: einmal täglich ``https://kidctl.de/version.json`` (von der Projektseite aus den GitHub-Releases erzeugt)
ohne Kennungen abrufen; abschaltbar mit ``UPDATE_CHECK=0``. Das Ergebnis liegt nur im Speicher."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx

from kidctl_common.protocol import PROTOCOL_VERSION

from . import i18n
from .config import settings
from .release_notes import RELEASES

VERSION: str = RELEASES[0]["version"]
RELEASE_DATE: date = date.fromisoformat(RELEASES[0]["date"])

HOMEPAGE = "https://kidctl.de/"
RELEASES_URL = "https://kidctl.de/releases/"
DOCS_URL = "https://kidctl.de/docs/"
LICENSE_URL = "https://www.gnu.org/licenses/agpl-3.0.html"
SECURITY_EMAIL = "security@kidctl.de"
VERSION_URL = "https://kidctl.de/version.json"
CHECK_SECONDS = 24 * 3600

log = logging.getLogger("kidctl.about")
_latest: str | None = None   # neueste veröffentlichte Version laut kidctl.de (None: unbekannt)


def version_key(version: str) -> tuple[int, ...]:
    """„0.45.2“ → (0, 45, 2); Unlesbares zählt als 0 (nie „neuer“)."""
    return tuple(int(x) for x in re.findall(r"\d+", version or "")[:4]) or (0,)


@dataclass(frozen=True)
class Note:
    version: str
    date: date
    items: list[str]


def _note(entry: dict) -> Note:
    lang = "en" if i18n.current() == "en" else "de"
    return Note(entry["version"], date.fromisoformat(entry["date"]), list(entry.get(lang) or entry["de"]))


def unseen_notes(parent) -> list[Note]:
    """Versionen seit der zuletzt bestätigten (neueste zuerst); leer ohne Elternteil oder ohne Stand."""
    seen = getattr(parent, "seen_version", "") if parent is not None else ""
    if not seen:
        return []
    return [_note(e) for e in RELEASES if version_key(e["version"]) > version_key(seen)]


def info() -> dict:
    """Inhalte des Info-Abschnitts (Eltern-Einstellungen und Verwaltungsbereich)."""
    newer = _latest if _latest and version_key(_latest) > version_key(VERSION) else None
    return {"version": VERSION, "release_date": RELEASE_DATE, "protocol": PROTOCOL_VERSION, "newer": newer,
            "update_check": settings.update_check, "notes": _note(RELEASES[0]),
            "homepage": HOMEPAGE, "releases_url": RELEASES_URL, "docs_url": DOCS_URL,
            "license_url": LICENSE_URL, "security_email": SECURITY_EMAIL, "source_url": settings.source_url}


async def check_latest(client: httpx.AsyncClient) -> str | None:
    """Neueste Version von kidctl.de holen und merken (Fehler: Stand bleibt)."""
    global _latest
    try:
        r = await client.get(VERSION_URL, timeout=15)
        r.raise_for_status()
        version = str(r.json().get("version", "")).lstrip("v")
        if re.fullmatch(r"\d+(\.\d+){1,3}", version):
            _latest = version
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        log.info("Versionsprüfung fehlgeschlagen: %s", e)
    return _latest


async def run_forever() -> None:
    if not settings.update_check:
        return
    async with httpx.AsyncClient(headers={"User-Agent": "kidctl"}, follow_redirects=True) as client:
        while True:
            await check_latest(client)
            await asyncio.sleep(CHECK_SECONDS)
