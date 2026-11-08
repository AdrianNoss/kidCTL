"""Sitzungen über die WTS-API (Ersatz für logind).

Gezählt wird wie unter Linux nur eine aktive, entsperrte Sitzung: Verbindungszustand ``WTSActive`` und
``SessionFlags`` = entsperrt. Getrennte Sitzungen (schneller Benutzerwechsel) und gesperrte zählen nicht.
Sitzung 0 (Dienste) und Sitzungen ohne Benutzer (Anmeldebildschirm) sind keine Benutzersitzungen.
"""

from __future__ import annotations

import asyncio
import logging

from ..sessions import SessionInfo
from . import api
from .accounts import Accounts

log = logging.getLogger("kidctl.wts")

TYPE = "windows"   # SessionInfo.type – gilt als grafisch (Abmeldung über den Desktop)


class WinSessions:
    def __init__(self, accounts: Accounts, backend=api) -> None:
        self.accounts = accounts
        self.api = backend

    def list(self) -> list[SessionInfo]:
        out = []
        for sid, state in self.api.enumerate_sessions():
            if sid == 0:
                continue
            try:
                info = self.api.session_info(sid)
            except OSError:
                continue  # Sitzung zwischenzeitlich beendet
            name = info.UserName
            if not name:
                continue  # Anmeldebildschirm
            account = self.accounts.by_name(name)
            if account is None:
                continue  # Domänen-/Fremdkonto: nie eingeschränkt
            out.append(SessionInfo(
                id=str(sid), uid=account.rid, user=account.wire, session_class="user",
                active=state == api.WTSActive, locked=info.SessionFlags == api.WTS_SESSIONSTATE_LOCK,
                state="active" if state == api.WTSActive else "online", type=TYPE, seat="",
                desktop="windows", leader=int(info.LogonTime)))
        return out

    def login_screens(self) -> list[int]:
        """Sitzungen, die gerade den Anmeldebildschirm zeigen: ohne angemeldeten Benutzer oder gesperrt (dort zeigt
        „Benutzer wechseln“ dieselbe Kontenliste); nicht Sitzung 0."""
        out = []
        for sid, state in self.api.enumerate_sessions():
            if sid == 0 or state not in (api.WTSActive, api.WTSConnected):
                continue
            try:
                info = self.api.session_info(sid)
                if not info.UserName or info.SessionFlags == api.WTS_SESSIONSTATE_LOCK:
                    out.append(sid)
            except OSError:
                continue
        return out

    async def sessions(self) -> list[SessionInfo]:
        return await asyncio.to_thread(self.list)

    async def terminate(self, session_id: str) -> None:
        log.warning("Melde Sitzung %s ab (WTSLogoffSession)", session_id)
        await asyncio.to_thread(self.api.logoff_session, int(session_id), False)
