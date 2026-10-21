"""Sprache der Texte für ein Kind: Sprache seiner Plasma-Sitzung (Umgebung von plasmashell bzw. eines
anderen Sitzungsprozesses), ohne Sitzung ``/etc/locale.conf``. Regel: ``de*`` -> Deutsch, sonst Englisch.
Lädt den englischen Katalog des Agenten."""

from __future__ import annotations

import pwd
from pathlib import Path

from kidctl_common import i18n
from kidctl_common.i18n import parse_locale_conf  # noqa: F401 – Weitergabe (Tests)

from .accounting import boottime
from .procs import pids_of, read_environ, read_proc

i18n.install(Path(__file__).parent / "locale" / "en.po")

LOCALE_CONF = Path("/etc/locale.conf")
# Prozesse der Sitzung, deren Startumgebung die in Plasma gewählte Sprache enthält (bevorzugt in dieser
# Reihenfolge). Nicht startplasma-*: Es setzt die Sprache aus plasma-localerc erst nach dem Start.
SESSION_PROCS = ("kidctl-tray", "plasmashell", "ksmserver", "kwin_wayland")
IGNORED = ("systemd", "sd-pam", "startplasma-wayland", "startplasma-x11", "sddm-helper")
CACHE_SECONDS = 60.0


def system_language(path: Path = LOCALE_CONF) -> str:
    return i18n.system_language(path)


class UserLanguages:
    """Sprache je Linux-User (zwischengespeichert, Sitzungen ändern sie selten)."""

    def __init__(self, list_pids=pids_of, read=read_proc, environ=read_environ, clock=boottime,
                 system=system_language) -> None:
        self.list_pids, self.read, self.environ, self.clock, self.system = list_pids, read, environ, clock, system
        self._cache: dict[str, tuple[float, str]] = {}

    def lang_of(self, user: str) -> str:
        now = self.clock()
        cached = self._cache.get(user)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        lang = self._session_language(user) or self.system()
        self._cache[user] = (now, lang)
        return lang

    def _session_language(self, user: str) -> str | None:
        """Sprache des am besten passenden Sitzungsprozesses (Tray-App zuerst – sie zeigt dem Kind dieselbe
        Sprache), sonst eines beliebigen anderen Prozesses des Users."""
        try:
            uid = pwd.getpwnam(user).pw_uid
        except KeyError:
            return None
        best: tuple[int, int] | None = None   # (Rang, PID)
        other = None
        for pid in self.list_pids({uid}):
            info = self.read(pid)
            if info is None:
                continue
            name = Path(info.exe).name
            if any(Path(a).name in ("kidctl-tray", "kidctl-tray-main.py") for a in info.argv[:6]):
                name = "kidctl-tray"   # Python-Skript: Programmname steht in den Argumenten
            if name in SESSION_PROCS:
                rank = SESSION_PROCS.index(name)
                if best is None or rank < best[0]:
                    best = (rank, pid)
            elif other is None and name not in IGNORED:
                other = pid
        for pid in (best[1] if best else None, other):
            if pid is not None and (lang := i18n.from_environ(self.environ(pid))):
                return lang
        return None
