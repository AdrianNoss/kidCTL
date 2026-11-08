"""Abgesicherter Modus: Kinder dürfen dort nicht arbeiten.

Im minimalen abgesicherten Modus lädt Windows keinen Netzwerk-Stack, schon ``import asyncio`` scheitert
(WinError 10050) – der normale Agent kann dort nicht laufen. Ein Kind erreicht den Modus ohne Admin vom
Anmeldebildschirm aus und hätte sonst keine Zeitbegrenzung. Deshalb startet der Dienst in beiden abgesicherten
Modi (mit und ohne Netzwerk) statt des Agenten diesen Wächter:

- alle Konten aus der gespeicherten Policy deaktivieren (``WinLoginLock``: nur fremd nicht deaktivierte, nie
  Admins); der Agent stellt sie beim nächsten normalen Start nach der Policy wieder her;
- Sitzungen dieser Konten nach einem kurzen Hinweis abmelden;
- nicht zugeordnete Konten (Eltern, Admins) bleiben unberührt, ohne gespeicherte Policy passiert nichts.

Dieses Modul darf ``asyncio`` nicht (indirekt) importieren.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading

from kidctl_common import i18n
from kidctl_common.i18n import _

from .. import store
from . import api, service
from .accounts import Accounts
from .langs import WinLanguages
from .loginlock import WinLoginLock

log = logging.getLogger("kidctl.safemode")

SM_CLEANBOOT = 67     # 0 = normal, 1 = abgesichert, 2 = abgesichert mit Netzwerk
TICK_SECONDS = 3.0
NOTICE_SECONDS = 10   # Hinweis vor der Abmeldung


def safe_boot() -> bool:
    return ctypes.WinDLL("user32").GetSystemMetrics(SM_CLEANBOOT) != 0


class Guard:
    def __init__(self, accounts: Accounts | None = None, backend=api) -> None:
        self.api = backend
        self.accounts = accounts or Accounts(backend)
        self.lock = WinLoginLock(self.accounts, backend)
        self.langs = WinLanguages(self.accounts)
        self._no_policy_logged = False

    def children(self) -> set[str]:
        """Zugeordnete Benutzer (Draht) aus der zuletzt gespeicherten Policy."""
        users = (store.load(store.POLICY_FILE) or {}).get("users") or {}
        if not users and not self._no_policy_logged:
            self._no_policy_logged = True
            log.warning("Keine zugeordneten Benutzer in der gespeicherten Policy – niemand wird gesperrt")
        return set(users)

    def tick(self) -> None:
        wires = self.children()
        self.lock.update(wires)
        try:
            sessions = self.api.enumerate_sessions()
        except OSError:
            log.exception("Sitzungen nicht lesbar")
            return
        for sid, _state in sessions:
            if sid != 0:
                self._check_session(sid, wires)
        self.lock.changed = False

    def _check_session(self, sid: int, wires: set[str]) -> None:
        try:
            user = self.api.session_info(sid).UserName
            if not user:
                if self.lock.changed:   # Der Anmeldebildschirm merkt sich die Kontenliste
                    self.api.kill_in_session("LogonUI.exe", sid)
                return
            account = self.accounts.by_name(user)
            if account is None or account.wire not in wires or self.accounts.is_admin(account):
                return
        except OSError:
            log.exception("Sitzung %s nicht prüfbar", sid)
            return
        log.warning("Melde %s ab (abgesicherter Modus)", account.wire)
        with i18n.use(self.langs.lang_of(account.wire)):
            title, body = _("Abgesicherter Modus"), _("Im abgesicherten Modus kannst du den Computer nicht benutzen. "
                                                      "Du wirst abgemeldet – starte den Computer bitte neu.")
        try:
            self.api.send_message(sid, title, body, NOTICE_SECONDS)
        except OSError:
            log.exception("Hinweis in Sitzung %s nicht anzeigbar", sid)
        try:
            self.api.logoff_session(sid, False)
        except OSError:
            log.exception("Sitzung %s nicht abmeldbar", sid)


def run() -> int:
    service.setup_logging(os.environ.get("KIDCTL_LOG", "INFO"))
    log.warning("Abgesicherter Modus: Wächter statt Agent – zugeordnete Kinder werden gesperrt")
    stop = threading.Event()

    def body(running) -> None:
        guard = Guard()
        running()
        while not stop.is_set():
            try:
                guard.tick()
            except Exception:
                log.exception("Wächter: Fehler im Durchlauf")
            stop.wait(TICK_SECONDS)

    service.dispatch(body, stop.set)
    return 0
