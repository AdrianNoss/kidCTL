"""Lokale Windows-Konten: Liste für den Server, Admin-Prüfung, Zuordnung Sitzung/Pipe -> Benutzername.

Auf dem Draht (Policy, Nutzung, Chat) steht wie unter Linux ein Benutzername im Linux-Format. Windows-Namen,
die dazu passen (meist: „lisa“, „Max“), werden unverändert verwendet, sonst ``rid-<RID>``. Als ``uid`` geht die
RID (relative ID, lokale Konten ab 1000) an den Server; intern wird über die SID zugeordnet (ändert sich nie,
auch nicht beim Umbenennen eines Kontos). Microsoft-Konten sind lokale Konten mit verknüpfter E-Mail-Adresse.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from kidctl_common import protocol as p

from . import api

log = logging.getLogger("kidctl.accounts")

ADMINS_SID = "S-1-5-32-544"   # lokale Gruppe Administratoren (Name ist lokalisiert)
CACHE_SECONDS = 30.0


@dataclass(frozen=True)
class Account:
    name: str        # Windows-Kontoname (SAM)
    rid: int
    sid: str
    full_name: str
    flags: int
    email: str = ""  # verknüpftes Microsoft-Konto

    @property
    def wire(self) -> str:
        return wire_name(self.name, self.rid)

    @property
    def disabled(self) -> bool:
        return bool(self.flags & api.UF_ACCOUNTDISABLE)


def wire_name(name: str, rid: int) -> str:
    """Benutzername auf dem Draht (siehe Moduldoku)."""
    return name if re.fullmatch(p.USERNAME_PATTERN, name) else f"rid-{rid}"


class Accounts:
    """Kontenliste mit kurzem Zwischenspeicher (der Takt fragt alle 5 s)."""

    def __init__(self, backend=api, clock=time.monotonic) -> None:
        self.api = backend
        self.clock = clock
        self._list: list[Account] = []
        self._at: float | None = None
        self._admins: dict[str, tuple[float, bool]] = {}
        self._admin_group: str | None = None

    def all(self, fresh: bool = False) -> list[Account]:
        """Normale lokale Konten mit RID ab 1000 (ohne Administrator, Gast, DefaultAccount …)."""
        now = self.clock()
        if fresh or self._at is None or now - self._at >= CACHE_SECONDS:
            out = []
            for name, full, rid, flags in self.api.user_enum():
                if rid < 1000:
                    continue
                try:
                    sid = self.api.account_sid(name)
                except OSError:
                    log.warning("SID von %s nicht ermittelbar", name)
                    continue
                out.append(Account(name, rid, sid, full, flags, self.api.user_internet_name(name)))
            self._list, self._at = out, now
        return self._list

    def by_wire(self, wire: str) -> Account | None:
        for a in self.all():
            if a.wire == wire:
                return a
        return None

    def by_sid(self, sid: str) -> Account | None:
        for a in self.all():
            if a.sid == sid:
                return a
        for a in self.all(fresh=True):  # neues Konto seit dem letzten Abruf
            if a.sid == sid:
                return a
        return None

    def by_name(self, name: str) -> Account | None:
        """Windows-Name (Groß-/Kleinschreibung egal, wie unter Windows)."""
        low = name.lower()
        for a in self.all():
            if a.name.lower() == low:
                return a
        return None

    def admin_group(self) -> str:
        if self._admin_group is None:
            self._admin_group = self.api.account_name(ADMINS_SID)[0]
        return self._admin_group

    def is_admin(self, account: Account) -> bool:
        """Mitglied der Gruppe Administratoren (auch indirekt). Im Zweifel (Fehler) Admin – nie aussperren."""
        now = self.clock()
        cached = self._admins.get(account.sid)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            group = self.admin_group().lower()
            admin = any(g.lower() == group for g in self.api.user_local_groups(account.name))
        except OSError:
            log.exception("Gruppen von %s nicht lesbar – gilt als Admin", account.name)
            admin = True
        self._admins[account.sid] = (now, admin)
        return admin

    def is_admin_wire(self, wire: str) -> bool:
        a = self.by_wire(wire)
        return a is not None and self.is_admin(a)

    def local_users(self) -> list[p.LocalUser]:
        out = []
        for a in self.all(fresh=True):
            label = a.full_name or a.name
            if a.email:
                label = f"{label} ({a.email})"
            out.append(p.LocalUser(username=a.wire, uid=a.rid, full_name=label[:255]))
        return out
