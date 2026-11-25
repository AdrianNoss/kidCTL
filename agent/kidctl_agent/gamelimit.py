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
                sig = getattr(signal, "SIGKILL", signal.SIGTERM)   # Windows: kein SIGKILL (Beenden ist dort hart)
            if sig is not None:
                try:
                    self.kill(ident[0], sig)
                except ProcessLookupError:
                    pass
        if new:
            log.info("%s von %s aufgebraucht – schließe %d Prozess(e)", self.what, user, len(processes))
            if now - self.last_notify.get(user, -1e9) >= NOTIFY_DEDUP:
                self.last_notify[user] = now
                with i18n.use(self.lang_of(user)):
                    title, body, action = self.done_texts()
                await self.desktop.notify(user, uid, title, body, critical=True, on_action=self.on_request, action=action)

    what = "Spielzeit"   # fürs Log

    def done_texts(self) -> tuple[str, str, str]:
        """(Titel, Text, Knopf) bei aufgebrauchter Zeit – in der Sprache des Users aufgerufen."""
        return (_("Spielzeit aufgebraucht"), _("Spiele werden für heute geschlossen. Du kannst um mehr Spielzeit bitten."),
                _("Mehr Spielzeit anfragen"))

    def warn_texts(self, minutes: int) -> tuple[str, str, str]:
        body = ngettext("Noch {n} Minute Spielzeit – danach werden Spiele geschlossen. Bitte speichern.",
                        "Noch {n} Minuten Spielzeit – danach werden Spiele geschlossen. Bitte speichern.",
                        minutes).format(n=minutes)
        return _("Spielzeit"), body, _("Mehr Spielzeit anfragen")

    async def _warn(self, user: str, uid: int, day: date, remaining: int, gaming: bool) -> None:
        warned = self.warned.setdefault((user, day), set())
        warned.intersection_update({m for m in WARN_MINUTES if remaining <= m * 60})  # mehr Zeit -> wieder scharf
        due = [m for m in WARN_MINUTES if remaining <= m * 60 and m not in warned]
        if due and gaming:
            warned.update(due)
            minutes = max(1, round(remaining / 60))
            with i18n.use(self.lang_of(user)):
                title, body, action = self.warn_texts(minutes)
            await self.desktop.notify(user, uid, title, body, critical=min(due) == 1, on_action=self.on_request,
                                      action=action)


class AppLimit(GameLimit):
    """Limit je Programm/Kategorie (K4): gleicher Ablauf wie das Spiele-Limit, eigene Texte, kein Anfrageknopf
    (keine eigene Gutschrift – das Kind fragt im Chat). ``label(user)`` = Name des Programms bzw. der Kategorie in
    der Sprache des Users."""

    def __init__(self, desktop, label, **kw) -> None:
        super().__init__(desktop, on_request=None, **kw)
        self.label = label
        self._user = ""

    @property
    def what(self) -> str:
        return f"Limit {self.label(self._user)}"

    async def step(self, user, uid, day, remaining, gaming, processes) -> None:
        self._user = user
        await super().step(user, uid, day, remaining, gaming, processes)

    def done_texts(self) -> tuple[str, str, str]:
        name = self.label(self._user)
        return (_("Zeit für {name} aufgebraucht").format(name=name),
                _("{name} wird für heute geschlossen. Du kannst deine Eltern im Chat fragen.").format(name=name), "")

    def warn_texts(self, minutes: int) -> tuple[str, str, str]:
        name = self.label(self._user)
        body = ngettext("Noch {n} Minute für {name} – danach wird es geschlossen. Bitte speichern.",
                        "Noch {n} Minuten für {name} – danach wird es geschlossen. Bitte speichern.",
                        minutes).format(n=minutes, name=name)
        return name, body, ""


class AppLimits:
    """Ein ``AppLimit`` je Limit-Schlüssel (eigener Zustand für Warnungen und beendete Prozesse)."""

    def __init__(self, desktop, kill=os.kill, clock=boottime, lang_of=lambda user: "de") -> None:
        self.desktop, self.kill, self.clock, self.lang_of = desktop, kill, clock, lang_of
        self.by_key: dict[str, AppLimit] = {}

    async def step(self, key: str, label, user: str, uid: int, day: date, remaining: int, running: bool,
                   processes: list[tuple[int, int]]) -> None:
        lim = self.by_key.get(key)
        if lim is None:
            lim = self.by_key[key] = AppLimit(self.desktop, label, kill=self.kill, clock=self.clock,
                                              lang_of=self.lang_of)
        lim.label = label
        await lim.step(user, uid, day, remaining, running, processes)

    def retain(self, keys: set[str]) -> None:
        """Limits, die es nicht mehr gibt, vergessen."""
        self.by_key = {k: v for k, v in self.by_key.items() if k in keys}
