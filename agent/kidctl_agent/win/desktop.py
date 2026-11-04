"""Aktionen in der Sitzung eines Kindes unter Windows (Ersatz für ``usersession.UserSession``).

- Benachrichtigungen zeigt die Tray-App (Toast); der Agent läuft als Dienst in Sitzung 0 ohne Desktop.
  Ist keine Tray-App verbunden, erscheinen **kritische** Meldungen (Zeitende, letzte Minute) als
  Meldungsfenster über ``WTSSendMessage`` – sonst bliebe die Abmeldung unangekündigt.
- Knöpfe („Verlängerung anfordern“): Die Tray-App meldet den Klick mit der ``action_id`` zurück, der Agent ruft
  dann wie unter Linux ``on_action(user)`` auf.
- Abmeldung per ``WTSLogoffSession`` (alle Sitzungen des Users).
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from kidctl_common import tray as t

from ..accounting import boottime
from . import api

log = logging.getLogger("kidctl.desktop")

ACTION_TTL = 900.0   # so lange bleibt ein Knopf gültig (wie ACTION_TIMEOUT unter Linux)
MESSAGE_TIMEOUT = 55  # Sekunden, dann schließt sich das Meldungsfenster selbst


class WinDesktop:
    def __init__(self, backend=api, clock=boottime) -> None:
        self.api = backend
        self.clock = clock
        self.agent = None   # WinAgent (Tray-Server, aktuelle Sitzungen)
        self._actions: dict[str, tuple[str, object, float]] = {}   # id -> (User, on_action, gültig bis)
        self._tasks: set[asyncio.Task] = set()

    def attach(self, agent) -> None:
        self.agent = agent

    def _session_ids(self, user: str) -> list[int]:
        return [int(s.id) for s in (self.agent._sessions if self.agent else []) if s.user == user]

    async def notify(self, user: str, uid: int, title: str, body: str, critical: bool = False,
                     on_action=None, action: str = "Verlängerung anfordern") -> None:
        now = self.clock()
        self._actions = {k: v for k, v in self._actions.items() if v[2] > now}
        action_id = None
        if on_action is not None:
            action_id = secrets.token_hex(8)
            self._actions[action_id] = (user, on_action, now + ACTION_TTL)
        msg = t.TrayNotify(title=title[:255], body=body[:2000], critical=critical, action_id=action_id,
                           action_label=action[:128] if on_action else "")
        delivered = self.agent is not None and await self.agent.tray.send_to_user(user, msg)
        if delivered or not critical:
            if not delivered:
                log.info("Keine Tray-App von %s verbunden – Benachrichtigung „%s“ entfällt", user, title)
            return
        for sid in self._session_ids(user):   # Rückfall: Meldungsfenster aus dem Dienst
            task = asyncio.create_task(self._message_box(sid, title, body))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _message_box(self, session_id: int, title: str, body: str) -> None:
        try:
            await asyncio.to_thread(self.api.send_message, session_id, title, body, MESSAGE_TIMEOUT)
        except OSError:
            log.exception("Meldung in Sitzung %s nicht anzeigbar", session_id)

    async def action(self, user: str, action_id: str) -> None:
        """Klick auf einen Knopf (von der Tray-App gemeldet). Nur der User, dem die Meldung galt."""
        entry = self._actions.get(action_id)
        if entry is None or entry[0] != user or entry[2] < self.clock():
            log.info("Unbekannte oder abgelaufene Aktion von %s", user)
            return
        try:
            await entry[1](user)
        except Exception:
            log.exception("Aktion für %s fehlgeschlagen", user)

    async def message(self, user: str, title: str, body: str) -> None:
        """Meldungsfenster in allen Sitzungen des Users (unabhängig von der Tray-App)."""
        for sid in self._session_ids(user):
            await self._message_box(sid, title, body)

    async def logout(self, user: str, uid: int, desktop: str = "") -> bool:
        ok = False
        for sid in self._session_ids(user):
            try:
                await asyncio.to_thread(self.api.logoff_session, sid, False)
                ok = True
            except OSError:
                log.exception("Sitzung %s von %s nicht abmeldbar", sid, user)
        return ok

    def running_desktop(self, uid: int) -> str | None:
        return None

    async def restart_display_manager(self) -> None:
        pass   # Windows zeigt nach der Abmeldung selbst den Anmeldebildschirm
