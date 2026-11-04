"""Login-Sperre unter Windows: Konto des Kindes deaktivieren (``UF_ACCOUNTDISABLE``), solange keine Zeit
verfügbar ist. Wirkt vor jeder Anmeldeart (Passwort, PIN, Windows Hello, Microsoft-Konto, abgesicherter Modus).

Anders als die PAM-Sperre fällt sie nicht von selbst weg, wenn der Agent ausfällt. Deshalb:
- kidctl merkt sich (``winlock.json``), welche Konten es selbst deaktiviert hat, und fasst nur diese wieder
  an – ein von den Eltern deaktiviertes Konto bleibt unberührt;
- die Absicht wird gespeichert, **bevor** das Konto deaktiviert wird (Absturz dazwischen -> wird beim nächsten
  Abgleich wieder aktiviert);
- Admins werden nie deaktiviert;
- ``release_all`` (Deinstallation, ``kidctl unlock-all``) aktiviert alle selbst deaktivierten Konten.
"""

from __future__ import annotations

import logging

from .. import store
from . import api
from .accounts import Accounts

log = logging.getLogger("kidctl.loginlock")

STATE_FILE = "winlock.json"


class WinLoginLock:
    def __init__(self, accounts: Accounts, backend=api) -> None:
        self.accounts = accounts
        self.api = backend
        data = store.load(STATE_FILE) or {}
        self.ours: set[str] = set(data.get("disabled", []))   # SIDs, die kidctl deaktiviert hat
        self._current: set[str] | None = None
        self.changed = False   # ein Konto wurde (de)aktiviert – Anmeldebildschirm aktualisieren

    def _save(self) -> None:
        store.save(STATE_FILE, {"disabled": sorted(self.ours)})

    def update(self, locked: set[str]) -> None:
        """``locked``: Benutzernamen (Draht), deren Anmeldung gesperrt sein soll."""
        want = {}
        for wire in locked:
            a = self.accounts.by_wire(wire)
            if a is not None and not self.accounts.is_admin(a):
                want[a.sid] = a
        for sid, a in want.items():
            self._disable(a)
        for sid in sorted(self.ours - set(want)):
            self._enable(sid)
        names = {a.wire for a in want.values()}
        if names != self._current:
            log.info("Login gesperrt für: %s", ", ".join(sorted(names)) or "niemanden")
            self._current = names

    def _disable(self, a) -> None:
        try:
            flags = self.api.user_flags(a.name)
        except OSError:
            log.exception("Konto %s nicht lesbar", a.name)
            return
        if flags & api.UF_ACCOUNTDISABLE:
            if a.sid not in self.ours:
                log.debug("%s ist schon (nicht von kidctl) deaktiviert – bleibt unberührt", a.name)
            return
        if a.sid not in self.ours:
            self.ours.add(a.sid)
            self._save()   # erst merken, dann deaktivieren
        try:
            self.api.set_user_flags(a.name, flags | api.UF_ACCOUNTDISABLE)
            self.changed = True
            log.warning("Konto %s deaktiviert (Login-Sperre)", a.name)
        except OSError:
            log.exception("Konto %s nicht deaktivierbar", a.name)

    def _enable(self, sid: str) -> None:
        a = self.accounts.by_sid(sid)
        if a is None:   # Konto gelöscht
            self.ours.discard(sid)
            self._save()
            return
        try:
            flags = self.api.user_flags(a.name)
            if flags & api.UF_ACCOUNTDISABLE:
                self.api.set_user_flags(a.name, flags & ~api.UF_ACCOUNTDISABLE)
                self.changed = True
                log.warning("Konto %s wieder aktiviert", a.name)
        except OSError:
            log.exception("Konto %s nicht aktivierbar – nächster Versuch im nächsten Takt", a.name)
            return
        self.ours.discard(sid)
        self._save()

    def locked_sids(self) -> set[str]:
        return set(self.ours)

    def release_all(self) -> int:
        """Alle von kidctl deaktivierten Konten wieder aktivieren. Rückgabe: Anzahl noch gesperrter."""
        for sid in sorted(self.ours):
            self._enable(sid)
        return len(self.ours)
