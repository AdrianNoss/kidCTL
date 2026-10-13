"""Durchsetzung: Vorwarnungen, 60-s-Countdown, Beenden der Sitzungen."""

from __future__ import annotations

import logging
from datetime import date

from .accounting import boottime
from .logind import SessionInfo
from .rules import REASON_TEXT, Decision

log = logging.getLogger("kidctl.enforcer")

WARN_MINUTES = (15, 5, 1)
GRACE_SECONDS = 60


class Enforcer:
    def __init__(self, logind, notifier, clock=boottime) -> None:
        self.logind = logind
        self.notifier = notifier
        self.clock = clock
        self.warned: dict[tuple[str, date], set[int]] = {}
        self.deadline: dict[str, float] = {}   # User -> Zeitpunkt (BOOTTIME) der Abmeldung

    async def step(self, day: date, sessions: list[SessionInfo], decisions: dict[str, Decision]) -> None:
        """``decisions`` enthält nur zugeordnete User; alle anderen bleiben unangetastet."""
        by_user: dict[str, list[SessionInfo]] = {}
        for s in sessions:
            if s.is_user_session and s.user in decisions:
                by_user.setdefault(s.user, []).append(s)

        for user in list(self.deadline):
            if user not in by_user:
                del self.deadline[user]  # abgemeldet

        for user, user_sessions in by_user.items():
            d = decisions[user]
            uid = user_sessions[0].uid
            if d.allowed:
                if self.deadline.pop(user, None) is not None:
                    await self.notifier.notify(user, uid, "Bildschirmzeit verlängert",
                                               f"Noch {_fmt(d.remaining_seconds)}.")
                await self._warn(user, uid, day, d.remaining_seconds)
                continue

            now = self.clock()
            if user not in self.deadline:
                self.deadline[user] = now + GRACE_SECONDS
                log.info("Zeit für %s abgelaufen (%s) – Abmeldung in %d s", user, d.reason, GRACE_SECONDS)
                await self.notifier.notify(
                    user, uid, "Bildschirmzeit zu Ende",
                    f"{REASON_TEXT.get(d.reason, '')} Abmeldung in {GRACE_SECONDS} Sekunden – "
                    "bitte jetzt speichern.", critical=True)
            elif now >= self.deadline[user]:
                del self.deadline[user]
                for s in user_sessions:
                    log.warning("Beende Sitzung %s von %s (%s)", s.id, user, d.reason)
                    try:
                        await self.logind.terminate(s.id)
                    except Exception:
                        log.exception("Sitzung %s konnte nicht beendet werden", s.id)

    async def _warn(self, user: str, uid: int, day: date, remaining: int) -> None:
        warned = self.warned.setdefault((user, day), set())
        # Mehr Zeit bekommen -> frühere Warnstufen wieder scharf
        warned.intersection_update({m for m in WARN_MINUTES if remaining <= m * 60})
        due = [m for m in WARN_MINUTES if remaining <= m * 60 and m not in warned]
        if due:
            warned.update(due)  # bei Anmeldung mit wenig Restzeit nur eine Warnung
            await self.notifier.notify(user, uid, "Bildschirmzeit",
                                       f"Noch {_fmt(remaining)} – danach wirst du abgemeldet.",
                                       critical=min(due) == 1)


def _fmt(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return "1 Minute" if minutes == 1 else f"{minutes} Minuten"
