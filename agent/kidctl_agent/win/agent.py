"""Windows-Agent (Schritt 1): Bildschirmzeit, Abmeldung, Login-Sperre, „Jetzt sperren“, Tray/Chat,
Integrität und Selbst-Update – auf dem gemeinsamen Kern (``core.AgentCore``)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from kidctl_common import i18n
from kidctl_common import protocol as p
from kidctl_common.i18n import _

from .. import __version__
from ..accounting import boottime
from ..core import AgentCore
from ..rules import Decision
from . import integrity
from .accounts import Accounts
from .desktop import WinDesktop
from .langs import WinLanguages
from .loginlock import WinLoginLock
from .pipeserver import PipeTrayServer
from .sessions import WinSessions
from .update import Updater

log = logging.getLogger("kidctl.agent")

BLOCKED_LOGIN_GRACE = 10   # Sekunden: Sitzung trotz Login-Sperre -> Hinweis, dann Abmeldung
TRAY_GRACE = 60            # Sekunden nach Erscheinen einer Sitzung: Zeit für den Autostart der Tray-App
TRAY_RETRY = 300           # frühestens so oft je Sitzung neu starten


def tray_command() -> tuple[str, str] | None:
    """(Befehlszeile, Arbeitsordner) der Tray-App neben dem Python des Dienstes; ``None`` außerhalb der
    Installation (Tests, Entwicklung)."""
    if sys.platform != "win32":
        return None
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return (f'"{pythonw}" -m kidctl_tray', str(pythonw.parent)) if pythonw.exists() else None


class WinAgent(AgentCore):
    admin_text = "Administrator"

    def __init__(self, sessions: WinSessions | None, desktop: WinDesktop | None, device: dict | None = None,
                 accounts: Accounts | None = None) -> None:
        self.accounts = accounts or (sessions.accounts if sessions else Accounts())
        super().__init__(sessions, desktop, device)
        self.tray = PipeTrayServer(self, self.accounts)
        self.langs = WinLanguages(self.accounts)
        self.loginlock = WinLoginLock(self.accounts, self.accounts.api)
        self.updater = Updater(device)
        self._known: set[tuple[str, int]] = set()   # bekannte Sitzungen (ID, Anmeldezeit)
        self._prev_locked: set[str] = set()          # im letzten Takt gesperrte User
        self._tasks: set[asyncio.Task] = set()
        self._seen_at: dict[tuple[str, int], float] = {}     # Sitzung -> zuerst gesehen
        self._tray_tried: dict[tuple[str, int], float] = {}  # Sitzung -> letzter Startversuch der Tray-App
        self.tray_command = tray_command()
        self.run_in_session = self.accounts.api.run_in_session if hasattr(self.accounts.api, "run_in_session") else None
        if desktop is not None:
            desktop.attach(self)

    # ---------- Plattform ----------

    def clock(self) -> float:
        return boottime()

    def is_admin_user(self, user: str) -> bool:
        return self.accounts.is_admin_wire(user)

    def local_users(self) -> list[p.LocalUser]:
        return self.accounts.local_users()

    # ---------- Takt ----------

    async def tick_once(self) -> None:
        await super().tick_once()
        self._prev_locked = self._locked_now

    @property
    def _locked_now(self) -> set[str]:
        return {u for u, d in self.decisions(self.now_local()).items() if not d.allowed}

    async def _maintain(self) -> None:
        """Rückfall der Login-Sperre: Erscheint eine Sitzung eines Users, der schon vorher gesperrt war (Konto
        deaktiviert), meldet der Agent sie nach kurzem Hinweis ab – ohne den üblichen 60-s-Countdown."""
        current = {(s.id, s.leader) for s in self._sessions}
        new = [s for s in self._sessions if (s.id, s.leader) not in self._known]
        self._known = current
        for s in new:
            if s.user not in self._prev_locked or s.user in self.enforcer.deadline:
                continue
            log.warning("Sitzung %s von %s trotz Login-Sperre – Abmeldung in %d s", s.id, s.user, BLOCKED_LOGIN_GRACE)
            self.enforcer.deadline[s.user] = self.enforcer.clock() + BLOCKED_LOGIN_GRACE
            self.enforcer.ending_reason[s.user] = None
            with i18n.use(self.lang_of(s.user)):
                title = _("Anmeldung gesperrt")
                body = _("Bildschirmzeit für heute aufgebraucht, außerhalb der erlaubten Zeit oder von den Eltern "
                         "gesperrt. Du wirst gleich abgemeldet.")
            task = asyncio.create_task(self.desktop.message(s.user, title, body))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _enforce_more(self, day, sessions, decisions: dict[str, Decision]) -> None:
        await self._ensure_trays(sessions, decisions)   # Spiele-Limit und Programmkontrolle: spätere Schritte

    async def _ensure_trays(self, sessions, decisions: dict[str, Decision]) -> None:
        """Tray-App in aktiven Kinder-Sitzungen starten, wenn keine verbunden ist (nach einem Update, das sie
        beendet hat, oder wenn das Kind sie geschlossen hat)."""
        if not self.tray_command or self.run_in_session is None:
            return
        now = self.clock()
        keys = {(s.id, s.leader) for s in sessions}
        self._seen_at = {k: v for k, v in self._seen_at.items() if k in keys}
        self._tray_tried = {k: v for k, v in self._tray_tried.items() if k in keys}
        connected = {c.user for c in self.tray.clients}
        for s in sessions:
            key = (s.id, s.leader)
            first = self._seen_at.setdefault(key, now)
            if not s.active or s.user not in decisions or s.user in connected or now - first < TRAY_GRACE:
                continue
            if now - self._tray_tried.get(key, -TRAY_RETRY) < TRAY_RETRY:
                continue
            self._tray_tried[key] = now
            cmd, cwd = self.tray_command
            try:
                pid = await asyncio.to_thread(self.run_in_session, int(s.id), cmd, cwd)
                log.info("Tray-App für %s in Sitzung %s gestartet (PID %s)", s.user, s.id, pid)
            except OSError:
                log.exception("Tray-App für %s nicht startbar", s.user)

    def _ready(self) -> None:
        log.info("KidCTL-Agent %s bereit", __version__)

    # ---------- Bericht, Integrität, Update ----------

    async def _report_more(self) -> None:
        await self.check_integrity()
        if self.online and self.updater.due():
            await asyncio.to_thread(self.updater.check)

    def _run_checks(self) -> list[p.IntegrityCheck]:
        assigned = set(self.policy.users) if self.policy else set()
        present = {a.wire for a in self.accounts.all()}
        return ([integrity.check_safeboot(), integrity.check_tray_autostart()]
                + integrity.check_tray(self.tray.tray_icons(), assigned)
                + integrity.check_admins(assigned & present, self.accounts.is_admin_wire))

    async def check_integrity(self) -> None:
        try:
            checks = await asyncio.to_thread(self._run_checks)
        except Exception:
            log.exception("Integritätsprüfung fehlgeschlagen")
            return
        await self.report_integrity(checks)
