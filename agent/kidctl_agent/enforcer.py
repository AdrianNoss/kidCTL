"""Durchsetzung: Vorwarnungen, 60-s-Countdown, Beenden der Sitzungen.

Grafische Sitzungen werden über Plasma abgemeldet (sonst bleibt SDDM ohne Anmeldebildschirm
zurück); erst wenn das nicht wirkt, hart per ``TerminateSession``.
"""

from __future__ import annotations

import logging
from datetime import date

from .accounting import boottime
from .logind import SessionInfo
from kidctl_common import i18n
from kidctl_common.i18n import _, ngettext

from .rules import LOCKED, REASON_TEXT, Decision

log = logging.getLogger("kidctl.enforcer")

WARN_MINUTES = (15, 5, 1)
GRACE_SECONDS = 60
LOGOUT_TIMEOUT = 20   # so lange auf die Plasma-Abmeldung warten, dann hart beenden
RECOVER_DELAY = 10    # danach prüfen, ob SDDM wieder einen Anmeldebildschirm zeigt


class Enforcer:
    def __init__(self, logind, desktop, clock=boottime, on_request=None, lang_of=lambda user: "de") -> None:
        self.lang_of = lang_of  # Sprache der Meldungen je User
        self.logind = logind
        self.desktop = desktop
        self.on_request = on_request  # async (user) -> None: Klick auf „Verlängerung anfordern“
        self.clock = clock
        self.warned: dict[tuple[str, date], set[int]] = {}
        self.deadline: dict[str, float] = {}   # User -> Ende des Countdowns (BOOTTIME)
        self.hard: dict[str, float] = {}       # User -> Abmeldung angefordert, hart beenden ab
        self.ending_reason: dict[str, str | None] = {}  # User -> Grund des laufenden Countdowns
        self.reminded: set[str] = set()        # Erinnerung 1 min vor Abmeldung (lange Vorwarnung)
        self.recover_at: float | None = None

    async def step(self, day: date, sessions: list[SessionInfo], decisions: dict[str, Decision]) -> None:
        """``decisions`` enthält nur zugeordnete User; alle anderen bleiben unangetastet."""
        by_user: dict[str, list[SessionInfo]] = {}
        for s in sessions:
            if s.is_user_session and s.user in decisions:
                by_user.setdefault(s.user, []).append(s)

        for pending in (self.deadline, self.hard):
            for user in list(pending):
                if user not in by_user:
                    del pending[user]  # abgemeldet
        self.reminded &= set(self.deadline)

        now = self.clock()
        for user, user_sessions in by_user.items():
            with i18n.use(self.lang_of(user)):   # Texte in der Sprache des Kindes
                d = decisions[user]
                uid = user_sessions[0].uid
                if d.allowed:
                    self.hard.pop(user, None)
                    if self.deadline.pop(user, None) is not None:
                        unlocked = self.ending_reason.pop(user, None) == LOCKED
                        await self.desktop.notify(user, uid, _("Wieder freigegeben") if unlocked else _("Bildschirmzeit verlängert"),
                                                  _("Noch {time}.").format(time=_fmt(d.remaining_seconds)))
                    await self._warn(user, uid, day, d.remaining_seconds)
                    continue

                if user not in self.deadline and user not in self.hard:
                    grace = GRACE_SECONDS if d.grace_seconds is None else d.grace_seconds
                    self.deadline[user] = now + grace
                    self.ending_reason[user] = d.reason
                    log.info("Zeit für %s abgelaufen (%s) – Abmeldung in %d s", user, d.reason, grace)
                    locked = d.reason == LOCKED
                    when = (_("Du wirst jetzt abgemeldet.") if grace <= 0 else
                            _("Abmeldung in {n} Sekunden – bitte jetzt speichern.").format(n=grace) if grace == GRACE_SECONDS else
                            _("Abmeldung in {time} – bitte rechtzeitig speichern.").format(time=_fmt(grace)))
                    await self.desktop.notify(
                        user, uid, _("Gesperrt") if locked else _("Bildschirmzeit zu Ende"),
                        f"{_(REASON_TEXT[d.reason]) if d.reason in REASON_TEXT else ''} {when}", critical=True,
                        on_action=None if locked else self.on_request)  # Verlängerung hebt keine Sperre auf
                    if grace <= 0:
                        del self.deadline[user]
                        await self._end(user, uid, user_sessions, d.reason, now)
                elif (user in self.deadline and user not in self.reminded
                      and 0 < self.deadline[user] - now <= 60
                      and self.ending_reason.get(user) == LOCKED and d.grace_seconds and d.grace_seconds > 60):
                    self.reminded.add(user)
                    await self.desktop.notify(user, uid, _("Gesperrt"), _("Abmeldung in 1 Minute – bitte jetzt speichern."),
                                              critical=True)
                elif user in self.deadline and now >= self.deadline[user]:
                    del self.deadline[user]
                    await self._end(user, uid, user_sessions, d.reason, now)
                elif user in self.hard and now >= self.hard[user]:
                    del self.hard[user]
                    log.warning("Plasma-Abmeldung von %s wirkt nicht – beende hart", user)
                    await self._terminate(user_sessions, d.reason)
                    if any(s.graphical for s in user_sessions):
                        self.recover_at = now + RECOVER_DELAY

        await self._recover_greeter(sessions, now)

    async def _end(self, user: str, uid: int, sessions: list[SessionInfo], reason, now: float) -> None:
        graphical = [s for s in sessions if s.graphical]
        await self._terminate([s for s in sessions if not s.graphical], reason)
        if graphical:
            log.warning("Melde %s von Plasma ab (%s)", user, reason)
            await self.desktop.logout(user, uid)
            self.hard[user] = now + LOGOUT_TIMEOUT

    async def _terminate(self, sessions: list[SessionInfo], reason) -> None:
        for s in sessions:
            log.warning("Beende Sitzung %s von %s (%s)", s.id, s.user, reason)
            try:
                await self.logind.terminate(s.id)
            except Exception:
                log.exception("Sitzung %s konnte nicht beendet werden", s.id)

    async def _recover_greeter(self, sessions: list[SessionInfo], now: float) -> None:
        """Nach hartem Beenden einer Desktop-Sitzung: Zeigt seat0 nichts mehr an (kein Greeter,
        keine andere Sitzung), Display-Manager neu starten. Trifft so niemanden."""
        if self.recover_at is None or now < self.recover_at:
            return
        self.recover_at = None
        on_seat = [s for s in sessions if s.seat == "seat0" and s.state != "closing"
                   and s.session_class in ("greeter", "user")]
        if not on_seat:
            await self.desktop.restart_display_manager()

    async def _warn(self, user: str, uid: int, day: date, remaining: int) -> None:
        warned = self.warned.setdefault((user, day), set())
        # Mehr Zeit bekommen -> frühere Warnstufen wieder scharf
        warned.intersection_update({m for m in WARN_MINUTES if remaining <= m * 60})
        due = [m for m in WARN_MINUTES if remaining <= m * 60 and m not in warned]
        if due:
            warned.update(due)  # bei Anmeldung mit wenig Restzeit nur eine Warnung
            await self.desktop.notify(user, uid, _("Bildschirmzeit"),
                                       _("Noch {time} – danach wirst du abgemeldet.").format(time=_fmt(remaining)),
                                       critical=min(due) == 1, on_action=self.on_request)


def _fmt(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return ngettext("{n} Minute", "{n} Minuten", minutes).format(n=minutes)
