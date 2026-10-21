"""Spiele-Limit durchsetzen: Vorwarnung 5 und 1 Minute vorher, danach Spiele schließen (erst SIGTERM,
nach ``KILL_AFTER`` Sekunden SIGKILL) und neu gestartete Spiele bis Tagesende gleich wieder schließen.
Der restliche Rechner bleibt nutzbar – das regelt die Bildschirmzeit (Enforcer).
"""

from __future__ import annotations

import logging
import os
import signal
from datetime import date

from kidctl_common import i18n
from kidctl_common.i18n import _, ngettext

from .accounting import boottime

log = logging.getLogger("kidctl.games")

WARN_MINUTES = (5, 1)
KILL_AFTER = 10.0     # Sekunden nach SIGTERM
NOTIFY_DEDUP = 60.0   # „Spielzeit aufgebraucht“ höchstens so oft


class GameLimit:
    def __init__(self, desktop, kill=os.kill, clock=boottime, on_request=None, lang_of=lambda user: "de") -> None:
        self.lang_of = lang_of
        self.desktop = desktop
        self.kill, self.clock = kill, clock
        self.on_request = on_request   # async (user) -> None: Klick auf „Mehr Spielzeit anfragen“
        self.warned: dict[tuple[str, date], set[int]] = {}
        self.termed: dict[str, dict[tuple[int, int], float]] = {}   # User -> (PID, Start) -> Zeitpunkt SIGTERM
        self.last_notify: dict[str, float] = {}

    async def step(self, user: str, uid: int, day: date, remaining: int | None, gaming: bool,
                   processes: list[tuple[int, int]]) -> None:
        """``remaining`` = Restspielzeit in Sekunden (``None`` = kein Limit), ``gaming`` = gerade läuft ein
        Spiel, ``processes`` = (PID, Start) der Spielprozesse (nur gebraucht, wenn aufgebraucht)."""
        if remaining is None:
            return
        if remaining > 0:
            await self._warn(user, uid, day, remaining, gaming)
            return
        now = self.clock()
        alive = set(processes)
        termed = self.termed[user] = {k: v for k, v in self.termed.get(user, {}).items() if k in alive}
        new = False
        for ident in processes:
            sent = termed.get(ident)
            sig = None
            if sent is None:
                sig, new = signal.SIGTERM, True
                termed[ident] = now
            elif now - sent >= KILL_AFTER:
                sig = signal.SIGKILL
            if sig is not None:
                try:
                    self.kill(ident[0], sig)
                except ProcessLookupError:
                    pass
        if new:
            log.info("Spielzeit von %s aufgebraucht – schließe %d Spielprozess(e)", user, len(processes))
            if now - self.last_notify.get(user, -1e9) >= NOTIFY_DEDUP:
                self.last_notify[user] = now
                with i18n.use(self.lang_of(user)):
                    title, body = _("Spielzeit aufgebraucht"), _("Spiele werden für heute geschlossen. Du kannst um mehr Spielzeit bitten.")
                    action = _("Mehr Spielzeit anfragen")
                await self.desktop.notify(user, uid, title, body, critical=True, on_action=self.on_request, action=action)

    async def _warn(self, user: str, uid: int, day: date, remaining: int, gaming: bool) -> None:
        warned = self.warned.setdefault((user, day), set())
        warned.intersection_update({m for m in WARN_MINUTES if remaining <= m * 60})  # mehr Zeit -> wieder scharf
        due = [m for m in WARN_MINUTES if remaining <= m * 60 and m not in warned]
        if due and gaming:
            warned.update(due)
            minutes = max(1, round(remaining / 60))
            with i18n.use(self.lang_of(user)):
                body = (ngettext("Noch {n} Minute Spielzeit – danach werden Spiele geschlossen. Bitte speichern.",
                                 "Noch {n} Minuten Spielzeit – danach werden Spiele geschlossen. Bitte speichern.",
                                 minutes).format(n=minutes))
                title, action = _("Spielzeit"), _("Mehr Spielzeit anfragen")
            await self.desktop.notify(user, uid, title, body, critical=min(due) == 1, on_action=self.on_request,
                                      action=action)
