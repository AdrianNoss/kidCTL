"""Programmkontrolle unter Windows: Prozesse zugeordneter Kinder prüfen, ggf. beenden, protokollieren, melden.

Wie unter Linux (``watcher.ProgramWatcher``): Modi Aus / Nur protokollieren / Sperren, Sperrprotokoll an den
Server, Benachrichtigung „Programm gesperrt“ mit Knopf „Freigabe anfragen“, Liste des kürzlich Gesperrten für
die Tray-App. Statt Netlink liest der Watcher die Prozessliste alle 0,5 s.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from kidctl_common import i18n
from kidctl_common import protocol as p
from kidctl_common import tray as t
from kidctl_common.i18n import _

from .. import store
from ..accounting import boottime
from . import catalog as catalog_mod
from . import procs as procs_mod
from .programs import Verdict, WinAllowed, classify, key_norm

log = logging.getLogger("kidctl.programs")

POLL = 0.5                 # Sekunden zwischen zwei Prozesslisten
CATALOG_EVERY = 6 * 3600.0
CATALOG_MIN_GAP = 60.0     # neu lesen bei unbekanntem Paket höchstens so oft
BLOCKED_FILE = "blocked.json"
MAX_PENDING = 500
REPORT_DEDUP = {"audit": 600.0, "enforce": 60.0, "domain": 600.0}
NOTIFY_DEDUP = 60.0
RECENT_SECONDS = 24 * 3600
RECENT_MAX = 100
# Kurz nach der Anmeldung startet Windows Hintergrund-Apps (Widgets, Edge-Vorstart …): gesperrt und
# protokolliert, aber ohne Benachrichtigung
LOGIN_QUIET_SECONDS = 90


class WinProgramWatcher:
    def __init__(self, agent, procs=procs_mod, scan_catalog=catalog_mod.scan, clock=boottime,
                 install_dir: str = "") -> None:
        self.agent = agent
        self.procs = procs
        self.scan_catalog = scan_catalog
        self.clock = clock
        self.install_dir = install_dir
        self.catalog: catalog_mod.Catalog | None = None
        self._catalog_at: float | None = None
        self._catalog_task: asyncio.Task | None = None
        self.names: dict[str, str] = {}
        self.names_en: dict[str, str] = {}
        self.canon: dict[str, str] = {}   # Normalform -> Schlüssel wie im Katalog (Schreibweise)
        self.handled: set[tuple[int, int]] = set()
        self._known_pids: set[int] = set()
        self.last_report: dict[tuple[str, str], float] = {}
        self.last_notify: dict[tuple[str, str], float] = {}
        self.recent: dict[str, dict[str, tuple[t.BlockedItem, float]]] = {}
        self.pending: list[p.BlockedEvent] = [
            p.BlockedEvent.model_validate(e) for e in (store.load(BLOCKED_FILE) or {}).get("events", [])]
        self._targets: dict[str, tuple[str, str, WinAllowed]] | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---------- Katalog ----------

    @property
    def apps(self) -> list[p.AppEntry]:
        return self.catalog.apps if self.catalog else []

    def catalog_due(self) -> bool:
        return self._catalog_at is None or self.clock() - self._catalog_at >= CATALOG_EVERY

    async def refresh_catalog(self) -> bool:
        """Katalog neu lesen (PowerShell, im Thread). True = geändert."""
        self._catalog_at = self.clock()
        kwargs = {"skip_dir": self.install_dir, "skip_profiles": self.admin_profiles()} if self.install_dir else {}
        try:
            cat = await asyncio.to_thread(self.scan_catalog, **kwargs)
        except Exception as e:
            log.error("Programmkatalog nicht lesbar: %s", e)
            return False
        changed = self.catalog is None or cat.apps != self.catalog.apps or cat.known_pfns != self.catalog.known_pfns
        self.catalog = cat
        self.names = {a.key: a.name for a in cat.apps}
        self.names_en = {a.key: a.name_en for a in cat.apps if a.name_en}
        self.canon = {key_norm(a.key): a.key for a in cat.apps}
        self.policy_changed()
        return changed

    def admin_profiles(self) -> list[str]:
        from .acl import profile_path
        accounts = self.agent.accounts
        return [path for a in accounts.all() if accounts.is_admin(a) and (path := profile_path(a.sid))]

    def _rescan_soon(self) -> None:
        """Unbekanntes Paket (gerade installiert): Katalog bald neu lesen und an den Server melden."""
        if self._catalog_task and not self._catalog_task.done():
            return
        if self._catalog_at is not None and self.clock() - self._catalog_at < CATALOG_MIN_GAP:
            return

        async def run() -> None:
            if await self.refresh_catalog() and self.agent.online:
                await self.agent.conn.send(p.ReportApps(apps=self.apps))
        self._catalog_task = asyncio.create_task(run())

    # ---------- Zielmenge ----------

    def policy_changed(self) -> None:
        self._targets = None
        self.handled.clear()

    def targets(self) -> dict[str, tuple[str, str, WinAllowed]]:
        """SID -> (User, Modus, Erlaubt) für zugeordnete Nicht-Admins mit Programmkontrolle."""
        if self._targets is None:
            out = {}
            pol = self.agent.policy
            if pol is not None:
                children = {c.id: c for c in pol.children}
                now = self.agent.now_local()
                eligible = self.agent.decisions(now)
                cat = self.catalog or catalog_mod.Catalog()
                for user, child_id in pol.users.items():
                    child = children.get(child_id)
                    if child is None or child.program_mode == "off" or user not in eligible:
                        continue
                    account = self.agent.accounts.by_wire(user)
                    if account is None:
                        continue
                    out[account.sid] = (user, child.program_mode, WinAllowed(
                        set(child.programs_on(now.date())), sid=account.sid, catalog_exes=cat.exes,
                        catalog_pfns=cat.pfns, known_pfns=cat.known_pfns, install_dir=self.install_dir))
            self._targets = out
        return self._targets

    def users(self) -> set[str]:
        return {user for user, _m, _a in self.targets().values()}

    # ---------- Prüfen ----------

    async def poll_forever(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                log.exception("Programmprüfung fehlgeschlagen")
            await asyncio.sleep(POLL)

    async def poll(self) -> None:
        if not self.targets():
            self._known_pids.clear()
            return
        snap = await asyncio.to_thread(self.procs.snapshot)
        new = [(pid, ppid) for pid, ppid in snap.items() if pid not in self._known_pids and pid > 4]
        self._known_pids = set(snap)
        if new:
            await self.check_many(new)

    async def scan(self) -> None:
        """Vollständiger Abgleich (Takt): alle Prozesse, auch nach Policy-Änderungen."""
        if not self.targets():
            return
        snap = await asyncio.to_thread(self.procs.snapshot)
        alive = await self.check_many([(pid, ppid) for pid, ppid in snap.items() if pid > 4])
        self.handled &= alive

    async def check_many(self, pids: list[tuple[int, int]]) -> set[tuple[int, int]]:
        infos = await asyncio.to_thread(lambda: [i for pid, ppid in pids if (i := self.procs.read(pid, ppid))])
        alive = set()
        for info in infos:
            alive.add(info.ident)
            await self.check(info)
        return alive

    async def check(self, info) -> None:
        targets = self.targets()
        if info.sid not in targets or info.ident in self.handled:
            return
        self.handled.add(info.ident)
        user, mode, allowed = targets[info.sid]
        if mode == "enforce" and self.catalog is None:
            mode = "audit"   # Sicherung: ohne Katalog nie beenden (Store-Pakete wären alle „unbekannt“)
        def parent_exe() -> str:
            parent = self.procs.read(info.ppid) if info.ppid else None
            return parent.exe if parent and parent.start < info.start else ""
        verdict = classify(info, allowed, owner=self.procs.file_owner, parent_exe=parent_exe)
        if verdict is None:
            return
        if verdict.unknown_package:
            self._rescan_soon()
        if (key := self.canon.get(key_norm(verdict.key))) and key != verdict.key:
            verdict = Verdict(key, verdict.name, verdict.detail, verdict.code, verdict.arg, verdict.grantable)
        name = self.names.get(verdict.key, verdict.name)
        if mode == "enforce":
            await asyncio.to_thread(self.procs.terminate, info.pid)
            if self._login_quiet(info):
                log.info("Beim Login gesperrt (ohne Benachrichtigung): %s von %s", name, user)
            else:
                if verdict.grantable:
                    self.remember(user, verdict.key, name)
                await self._notify(user, verdict.key, name, verdict.grantable)
        new = self._record(user, verdict, name, audit=mode != "enforce")
        log.log(logging.WARNING if new else logging.DEBUG, "%s: %s (%s%s) von %s, PID %s; Aufruf: %s",
                "Gesperrt" if mode == "enforce" else "Protokolliert (würde sperren)", name, verdict.key,
                f", {verdict.detail}" if verdict.detail else "", user, info.pid, info.cmdline[:300])

    def _login_quiet(self, info) -> bool:
        """Prozess kurz nach der Anmeldung der Sitzung gestartet (Autostart des Systems)."""
        for s in getattr(self.agent, "_sessions", []):
            if str(info.session) == s.id and s.leader:
                return 0 <= (info.start - s.leader) / 1e7 < LOGIN_QUIET_SECONDS
        return False

    # ---------- Melden ----------

    async def _notify(self, user: str, key: str, name: str, grantable: bool = True) -> None:
        now = self.clock()
        if now - self.last_notify.get((user, key), -1e9) < NOTIFY_DEDUP:
            return
        self.last_notify[(user, key)] = now
        with i18n.use(self.agent.lang_of(user)):
            name = self.display_name(key, name)
            title = _("Programm gesperrt")
            if not grantable:
                body, action = _("„{name}“ ist nicht freigegeben.").format(name=name), None
            else:
                body = _("„{name}“ ist nicht freigegeben. Du kannst deine Eltern um Freigabe bitten.").format(name=name)
                action = _("Freigabe anfragen")
        if action is None:
            await self.agent.desktop.notify(user, 0, title, body)
            return

        async def ask(u: str) -> None:
            await self.agent.open_access_dialog(u, key)
        await self.agent.desktop.notify(user, 0, title, body, on_action=ask, action=action)

    def display_name(self, key: str, fallback: str) -> str:
        if i18n.current() == "en" and (en := self.names_en.get(key)):
            return en
        return self.names.get(key, fallback)

    def remember(self, user: str, key: str, name: str, kind: str = "program") -> None:
        items = self.recent.setdefault(user, {})
        old = items.pop(key, None)
        item = t.BlockedItem(access_kind=kind, key=key, name=name, last_at=datetime.now(timezone.utc),
                             count=old[0].count + 1 if old else 1)
        items[key] = (item, self.clock())
        while len(items) > RECENT_MAX:
            del items[next(iter(items))]

    def recent_items(self, user: str) -> list[t.BlockedItem]:
        now = self.clock()
        items = self.recent.get(user, {})
        for key in [k for k, (_i, at) in items.items() if now - at > RECENT_SECONDS]:
            del items[key]
        return [i for i, _at in sorted(items.values(), key=lambda x: -x[1])]

    def record_domain(self, user: str, domain: str, source: str) -> bool:
        """Vom Website-Filter gesperrte Anfrage: ins Protokoll, Hauptdomain für „Freigabe anfragen“."""
        from ..dnsfilter import site_of, source_code
        if source != "DNS-over-HTTPS-Anbieter":
            site = site_of(domain)
            self.remember(user, f"domain:{site}", site, kind="domain")
        code, arg = source_code(source)
        return self._record(user, Verdict(f"domain:{domain}", domain, source, code=code, arg=arg), domain,
                            audit=False, kind="domain")

    def _record(self, user: str, verdict: Verdict, name: str, audit: bool, kind: str = "program") -> bool:
        now = self.clock()
        k = (user, verdict.key)
        dedup = REPORT_DEDUP["domain" if kind == "domain" else "audit" if audit else "enforce"]
        if now - self.last_report.get(k, -1e9) < dedup:
            return False
        self.last_report[k] = now
        self.pending.append(p.BlockedEvent(username=user, at=datetime.now(timezone.utc), kind=kind,
                                           key=verdict.key[:512], name=name[:255], detail=verdict.detail[:255],
                                           audit=audit, detail_code=verdict.code[:32], detail_arg=verdict.arg[:255]))
        del self.pending[:-MAX_PENDING]
        store.save(BLOCKED_FILE, {"events": [e.model_dump(mode="json") for e in self.pending]})
        return True

    async def flush(self, conn) -> None:
        if not self.pending or conn is None or not conn.connected:
            return
        batch = list(self.pending)
        if await conn.send(p.BlockedReport(events=batch)):
            self.pending = self.pending[len(batch):]
            store.save(BLOCKED_FILE, {"events": [e.model_dump(mode="json") for e in self.pending]})
