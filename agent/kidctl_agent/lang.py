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
SESSION_PROCS = ("plasmashell", "ksmserver", "kwin_wayland", "startplasma-wayland", "kidctl-tray")
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
        try:
            uid = pwd.getpwnam(user).pw_uid
        except KeyError:
            return None
        fallback = None
        for pid in self.list_pids({uid}):
            info = self.read(pid)
            if info is None:
                continue
            name = Path(info.exe).name
            if name in SESSION_PROCS or (fallback is None and name not in ("systemd", "sd-pam")):
                lang = i18n.from_environ(self.environ(pid))
                if lang and name in SESSION_PROCS:
                    return lang
                fallback = fallback or lang
        return fallback
