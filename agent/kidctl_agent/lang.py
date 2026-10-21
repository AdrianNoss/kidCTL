"""Sprache der Texte für ein Kind: Sprache seiner Desktop-Sitzung (Umgebung von plasmashell, gnome-shell bzw.
eines anderen Sitzungsprozesses), ohne Sitzung die in GNOME gewählte Sprache (AccountsService), sonst
``/etc/locale.conf``. Regel: ``de*`` -> Deutsch, sonst Englisch. Lädt den englischen Katalog des Agenten."""

from __future__ import annotations

import pwd
from pathlib import Path

from kidctl_common import i18n
from kidctl_common.i18n import parse_locale_conf  # noqa: F401 – Weitergabe (Tests)

from .accounting import boottime
from .procs import pids_of, read_environ, read_proc

i18n.install(Path(__file__).parent / "locale" / "en.po")

LOCALE_CONF = Path("/etc/locale.conf")
ACCOUNTS_DIR = Path("/var/lib/AccountsService/users")
# Prozesse der Sitzung, deren Startumgebung die im Desktop gewählte Sprache enthält (bevorzugt in dieser
# Reihenfolge). Nicht startplasma-*: Es setzt die Sprache aus plasma-localerc erst nach dem Start.
# GNOME übernimmt die Sprache beim Login aus AccountsService in die Sitzungsumgebung.
SESSION_PROCS = ("kidctl-tray", "plasmashell", "gnome-shell", "ksmserver", "kwin_wayland", "gnome-session-binary")
IGNORED = ("systemd", "sd-pam", "startplasma-wayland", "startplasma-x11", "sddm-helper",
           "gdm-wayland-session", "gdm-x-session")
CACHE_SECONDS = 60.0


def system_language(path: Path = LOCALE_CONF) -> str:
    return i18n.system_language(path)


def accounts_language(user: str, accounts_dir: Path = ACCOUNTS_DIR) -> str | None:
    """In den GNOME-Kontoeinstellungen gewählte Sprache (AccountsService: ``Languages=a;b;`` bzw. älter
    ``Language=a``, erste gilt), sonst ``None``."""
    try:
        text = (accounts_dir / user).read_text(errors="replace")
    except (OSError, ValueError):
        return None
    for line in text.splitlines():
        key, _sep, value = line.partition("=")
        if key.strip() in ("Languages", "Language") and (lang := value.split(";")[0].strip()):
            return i18n.normalize(lang)
    return None


class UserLanguages:
    """Sprache je Linux-User (zwischengespeichert, Sitzungen ändern sie selten)."""

    def __init__(self, list_pids=pids_of, read=read_proc, environ=read_environ, clock=boottime,
                 system=system_language, accounts=accounts_language) -> None:
        self.list_pids, self.read, self.environ, self.clock, self.system = list_pids, read, environ, clock, system
        self.accounts = accounts
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
        if best and (lang := i18n.from_environ(self.environ(best[1]))):
            return lang
        if lang := self.accounts(user):
            return lang
        if other is not None and (lang := i18n.from_environ(self.environ(other))):
            return lang
        return None
