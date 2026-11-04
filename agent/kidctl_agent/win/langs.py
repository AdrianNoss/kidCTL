"""Sprache der Meldungen je Kind unter Windows: Anzeigesprache des Kontos (Registry des angemeldeten Users,
``HKEY_USERS\\<SID>``), sonst Systemsprache. Sprachregel wie überall: ``de*`` -> Deutsch, sonst Englisch."""

from __future__ import annotations

import logging
import time

from kidctl_common import i18n

from . import api
from .accounts import Accounts

log = logging.getLogger("kidctl.lang")

CACHE_SECONDS = 60.0
# (Schlüssel unter HKEY_USERS\<SID>, Wert) in dieser Reihenfolge
SOURCES = ((r"Control Panel\Desktop", "PreferredUILanguages"),
           (r"Control Panel\Desktop\MuiCached", "MachinePreferredUILanguages"),
           (r"Control Panel\International", "LocaleName"))


def system_language() -> str:
    try:
        return "de" if api.system_ui_language() & 0x3FF == 0x07 else "en"
    except OSError:
        return "de"


def user_language_raw(sid: str) -> str | None:
    import winreg

    for key, value in SOURCES:
        try:
            with winreg.OpenKey(winreg.HKEY_USERS, rf"{sid}\{key}") as k:
                data, _typ = winreg.QueryValueEx(k, value)
        except OSError:
            continue   # Profil nicht geladen (nicht angemeldet) oder Wert fehlt
        if isinstance(data, list):
            data = data[0] if data else ""
        if data:
            return str(data)
    return None


class WinLanguages:
    def __init__(self, accounts: Accounts, read=user_language_raw, clock=time.monotonic) -> None:
        self.accounts = accounts
        self.read = read
        self.clock = clock
        self._cache: dict[str, tuple[float, str]] = {}
        self._system: str | None = None

    def lang_of(self, user: str) -> str:
        now = self.clock()
        hit = self._cache.get(user)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        a = self.accounts.by_wire(user)
        raw = self.read(a.sid) if a else None
        if raw:
            lang = i18n.normalize(raw)
        else:
            if self._system is None:
                self._system = system_language()
            lang = self._system
        self._cache[user] = (now, lang)
        return lang
