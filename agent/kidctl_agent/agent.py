"""Steuerung: Takt (Zählen + Durchsetzen), Policy-Cache, Server-Synchronisation."""

from __future__ import annotations

import asyncio
import logging
import pwd
from datetime import datetime
from zoneinfo import ZoneInfo

from kidctl_common import protocol as p

from . import store
from .accounting import Usage, boottime
from .connection import Connection
from .enforcer import Enforcer
from .rules import Decision, evaluate

log = logging.getLogger("kidctl.agent")

TICK = 5.0            # Sekunden
SAVE_EVERY = 6        # Takte (= 30 s)
REPORT_EVERY = 60.0   # Sekunden
# Nach dem Start kurz auf die aktuelle Policy vom Server warten, bevor durchgesetzt wird –
# sonst kann die (ältere) gecachte Policy einen Fehlalarm auslösen. Gezählt wird trotzdem.
STARTUP_SYNC = 10.0   # Sekunden
DEFAULT_TZ = "Europe/Berlin"


def local_users() -> list[p.LocalUser]:
    return [p.LocalUser(username=u.pw_name, uid=u.pw_uid, full_name=u.pw_gecos.split(",")[0][:255])
            for u in pwd.getpwall() if 1000 <= u.pw_uid < 60000]


class Agent:
    def __init__(self, logind, desktop, device: dict | None = None) -> None:
        self.logind = logind
        self.enforcer = Enforcer(logind, desktop)
        self.usage = Usage.load()
        raw = store.load(store.POLICY_FILE)
        self.policy: p.Policy | None = p.Policy.model_validate(raw) if raw else None
        self.conn = Connection(device, self) if device else None
        self._ticks = 0
        self._started = boottime()
        self._synced = self.conn is None  # ohne Server: sofort gecachte Policy durchsetzen

    # ---------- Hilfen ----------

    def now_local(self) -> datetime:
        tz = self.policy.timezone if self.policy else DEFAULT_TZ
        return datetime.now(ZoneInfo(tz))

    def decisions(self, now: datetime) -> dict[str, Decision]:
        """Entscheidung je zugeordnetem User (nicht zugeordnete fehlen -> nie eingeschränkt)."""
        if self.policy is None:
            return {}
        children = {c.id: c for c in self.policy.children}
        out = {}
        for user, child_id in self.policy.users.items():
            child = children.get(child_id)
            if child is None:
                continue
            same_child = [u for u, c in self.policy.users.items() if c == child_id]
            used = self.usage.used_seconds(child_id, same_child, now.date())
            out[user] = evaluate(child, now, used)
        return out

    # ---------- Takt ----------

    async def tick_once(self) -> None:
        sessions = await self.logind.sessions()
        now = self.now_local()
        mapped = set(self.policy.users) if self.policy else set()
        counting = {s.user for s in sessions if s.counts and s.user in mapped}
        self.usage.tick(counting, now.date())
        if self._synced or boottime() - self._started >= STARTUP_SYNC:
            await self.enforcer.step(now.date(), sessions, self.decisions(now))
        self._ticks += 1
        if self._ticks % SAVE_EVERY == 0:
            self.usage.prune(now.date())
            self.usage.save()

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.tick_once()
            except Exception:
                log.exception("Fehler im Takt")
            await asyncio.sleep(TICK)

    async def _report_loop(self) -> None:
        while True:
            await asyncio.sleep(REPORT_EVERY)
            await self.send_usage()

    async def send_usage(self) -> None:
        if self.conn is None or not self.conn.connected:
            return
        entries = [p.UsageEntry(username=u, day=d, seconds=s)
                   for u, d, s in self.usage.report_entries(self.now_local().date())]
        if entries:
            await self.conn.send(p.UsageReport(entries=entries))

    # ---------- Server-Ereignisse ----------

    async def on_connected(self, conn: Connection) -> None:
        await conn.send(p.ReportUsers(users=local_users()))
        await self.send_usage()

    async def on_message(self, msg) -> None:
        if isinstance(msg, p.Policy):
            self.policy = msg
            self._synced = True
            store.save(store.POLICY_FILE, msg.model_dump(mode="json"))
            log.info("Policy %s übernommen (%d zugeordnete User)", msg.version, len(msg.users))
            await self.conn.send(p.PolicyAck(version=msg.version))
        elif isinstance(msg, p.UsageUpdate):
            self.usage.others[(msg.child_id, msg.day.isoformat())] = msg.others_seconds
        elif isinstance(msg, p.Ping):
            await self.conn.send(p.Pong())

    # ---------- Lauf ----------

    async def run(self) -> None:
        if self.conn is None:
            log.warning("Gerät nicht gekoppelt – nur gecachte Policy wird durchgesetzt")
        if self.policy is None:
            log.warning("Keine Policy vorhanden – niemand wird eingeschränkt")
        tasks = [asyncio.create_task(self._tick_loop())]
        if self.conn is not None:
            tasks += [asyncio.create_task(self.conn.run_forever()),
                      asyncio.create_task(self._report_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self.usage.save()
