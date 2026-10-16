"""Steuerung: Takt (Zählen + Durchsetzen), Policy-Cache, Server-Synchronisation, Tray/Chat."""

from __future__ import annotations

import asyncio
import logging
import pwd
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from kidctl_common import protocol as p
from kidctl_common import tray as t

from . import integrity, sdnotify, store
from .accounting import Usage, boottime
from .apps import scan_apps
from .apptime import AppIndex, AppTime
from .chat import NEW, ChatStore, Outbox, new_client_msg_id
from .connection import Connection
from .dnsfilter import DnsFilter
from .enforcer import Enforcer
from .kidmounts import STEAM_ROOT, KidMounts
from .loginlock import LoginLock, is_admin
from .procs import ProcEvents, pids_of, read_proc
from .rules import Decision, day_limits, evaluate
from .trayserver import TrayServer
from .watcher import ProgramWatcher

log = logging.getLogger("kidctl.agent")

TICK = 5.0            # Sekunden
SAVE_EVERY = 6        # Takte (= 30 s)
REPORT_EVERY = 60.0   # Sekunden
APPS_EVERY = 6 * 3600.0  # installierte Anwendungen neu einlesen
# Nach dem Start kurz auf die aktuelle Policy vom Server warten, bevor durchgesetzt wird –
# sonst kann die (ältere) gecachte Policy einen Fehlalarm auslösen. Gezählt wird trotzdem.
STARTUP_SYNC = 10.0   # Sekunden
DEFAULT_TZ = "Europe/Berlin"


def local_users() -> list[p.LocalUser]:
    return [p.LocalUser(username=u.pw_name, uid=u.pw_uid, full_name=u.pw_gecos.split(",")[0][:255])
            for u in pwd.getpwall()
            if 1000 <= u.pw_uid < 60000 and re.fullmatch(p.USERNAME_PATTERN, u.pw_name)]


class Agent:
    def __init__(self, logind, desktop, device: dict | None = None, tray_path: str | None = None) -> None:
        self.logind = logind
        self.desktop = desktop
        self.enforcer = Enforcer(logind, desktop, on_request=self.open_request_dialog)
        self.usage = Usage.load()
        self.apptime = AppTime.load()
        self.app_index = AppIndex(set())
        self.chat = ChatStore.load()
        self.outbox = Outbox.load()
        self.tray = TrayServer(self, tray_path)
        self.programs = ProgramWatcher(self)
        self.mounts = KidMounts()
        self.dns = DnsFilter()
        self.device = device
        self._integrity: list[p.IntegrityCheck] | None = None  # zuletzt gemeldet
        self.procevents = ProcEvents(self.programs.on_exec)
        self.apps: list[p.AppEntry] = []
        self._sessions: list = []
        raw = store.load(store.POLICY_FILE)
        self.policy: p.Policy | None = p.Policy.model_validate(raw) if raw else None
        try:
            self.conn = Connection(device, self) if device else None
        except ValueError as e:  # z. B. http:// ohne --insecure-http: offline weiter durchsetzen
            log.error("Keine Serververbindung: %s", e)
            self.conn = None
        self._ticks = 0
        self.loginlock = LoginLock()
        self._admin_warned: set[str] = set()
        self._started = boottime()
        self._synced = self.conn is None  # ohne Server: sofort gecachte Policy durchsetzen

    # ---------- Hilfen ----------

    def now_local(self) -> datetime:
        tz = self.policy.timezone if self.policy else DEFAULT_TZ
        return datetime.now(ZoneInfo(tz))

    @property
    def online(self) -> bool:
        return self.conn is not None and self.conn.connected

    def users_of(self, child_id: int) -> list[str]:
        return [u for u, c in (self.policy.users.items() if self.policy else []) if c == child_id]

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
            if is_admin(user):  # Grundregel: Admin-Accounts nie einschränken
                if user not in self._admin_warned:
                    log.warning("%s ist Admin (wheel/sudo) – Zuordnung zu Kind %s wird ignoriert",
                                user, child_id)
                    self._admin_warned.add(user)
                continue
            same_child = [u for u, c in self.policy.users.items() if c == child_id]
            used = self.usage.used_seconds(child_id, same_child, now.date())
            out[user] = evaluate(child, now, used)
        return out

    # ---------- Takt ----------

    async def tick_once(self) -> None:
        sessions = await self.logind.sessions()
        self._sessions = sessions
        now = self.now_local()
        decisions = self.decisions(now)  # nur zugeordnete Nicht-Admins
        counting = {s.user for s in sessions if s.counts and s.user in decisions}
        delta = self.usage.tick(counting, now.date())
        if delta:
            self._count_apps(counting, now.date(), delta)
        self._collect_blocked_domains()
        # Login-Sperre sofort (auch mit gecachter Policy) – sonst wäre direkt nach dem Booten
        # kurz eine Anmeldung möglich. Gilt auch ohne laufende Sitzung.
        self.loginlock.update({u for u, d in decisions.items() if not d.allowed})
        if self.mounts.releasable():  # aufgeschobenes Aushängen nach der Abmeldung nachholen
            await self.sync_mounts()
        if self._synced or boottime() - self._started >= STARTUP_SYNC:
            await self.enforcer.step(now.date(), sessions, decisions)
            await self.programs.scan()
        await self.tray.push_states(boottime())
        self._ticks += 1
        if self._ticks % SAVE_EVERY == 0:
            self.usage.prune(now.date())
            self.usage.save()
            self.apptime.prune(now.date())
            self.apptime.save()

    async def _tick_loop(self) -> None:
        ready = False
        while True:
            try:
                await self.tick_once()
            except Exception:
                log.exception("Fehler im Takt")
            if not ready:
                # Erst nach dem ersten Takt (Sperrliste geschrieben) bereit melden – die Unit
                # liegt vor systemd-user-sessions.service, Anmeldungen sind also erst danach möglich.
                ready = True
                sdnotify.notify("READY=1")
            await asyncio.sleep(TICK)

    async def _report_loop(self) -> None:
        last_apps = boottime()
        while True:
            await asyncio.sleep(REPORT_EVERY)
            await self.send_usage()
            await self.programs.flush(self.conn)
            await self.check_integrity()
            if boottime() - last_apps >= APPS_EVERY or self.programs.base_outdated():
                last_apps = boottime()  # nach pacman-Updates: Apps und Basisliste neu
                self.refresh_apps()
                await self.programs.refresh_base(self.app_exes())
                if self.online:
                    await self.conn.send(p.ReportApps(apps=self.apps))

    def refresh_apps(self) -> None:
        try:
            self.apps = scan_apps()
        except OSError:
            log.exception("Anwendungen nicht lesbar")
            return
        self.programs.names = {a.key: a.name for a in self.apps}
        self.app_index = AppIndex({a.key for a in self.apps})

    async def sync_mounts(self) -> None:
        """noexec-Homes und Steam-Bibliotheken an die Policy angleichen (systemd, im Thread)."""
        users = self.programs.users()
        try:
            await asyncio.to_thread(self.mounts.sync, users)
            await asyncio.to_thread(self.mounts.ensure_steam, users)
        except Exception:
            log.exception("Mounts nicht angleichbar")

    async def sync_dns(self) -> None:
        """Website-Filter an die Policy angleichen: Listen holen, Resolver + nftables (im Thread)."""
        if self.policy is None:
            return
        eligible = self.decisions(self.now_local())  # zugeordnete Nicht-Admins
        children = {c.id: (c.web, {u for u in self.users_of(c.id) if u in eligible})
                    for c in self.policy.children}
        refs = [r for web, _ in children.values() if web.mode == "blacklist" for r in web.lists]
        try:
            await asyncio.to_thread(self.dns.fetch_lists, refs, self.device)
            await asyncio.to_thread(self.dns.apply, children)
        except Exception:
            log.exception("Website-Filter nicht anwendbar")

    def web_children(self) -> dict[int, set[str]]:
        if self.policy is None:
            return {}
        eligible = self.decisions(self.now_local())
        out = {}
        for c in self.policy.children:
            users = {u for u in self.users_of(c.id) if u in eligible}
            if c.web.mode != "off" and users:
                out[c.id] = users
        return out

    def _run_checks(self) -> list[p.IntegrityCheck]:
        return ([integrity.check_pam(), integrity.check_nsswitch(), integrity.check_browser_policies()]
                + integrity.check_noexec(self.programs.users())
                + integrity.check_dns(self.web_children()))

    async def check_integrity(self) -> None:
        """Prüfen, Selbstverwaltetes (nftables, Resolver, Mounts) reparieren, bei Änderung melden."""
        try:
            checks = await asyncio.to_thread(self._run_checks)
            broken = [c for c in checks if not c.ok and c.id.split(":")[0] in ("dns", "home", "noexec")]
            if broken:
                log.warning("Integrität: %s – versuche Wiederherstellung",
                            "; ".join(f"{c.label}: {c.detail}" for c in broken))
                self.dns.reset()
                await self.sync_mounts()
                await self.sync_dns()
                checks = await asyncio.to_thread(self._run_checks)
        except Exception:
            log.exception("Integritätsprüfung fehlgeschlagen")
            return
        failed = [c for c in checks if not c.ok]
        if checks != self._integrity:
            for c in failed:
                log.error("Integrität verletzt: %s – %s", c.label, c.detail)
            if self.online and await self.conn.send(p.IntegrityReport(checks=checks)):
                self._integrity = checks

    def app_exes(self) -> set[str]:
        return {a.key[4:] for a in self.apps if a.key.startswith("exe:")}

    async def send_usage(self) -> None:
        if self.conn is None or not self.conn.connected:
            return
        today = self.now_local().date()
        entries = [p.UsageEntry(username=u, day=d, seconds=s) for u, d, s in self.usage.report_entries(today)]
        if entries:
            await self.conn.send(p.UsageReport(entries=entries))
        apps = [p.AppUsageEntry(username=u, day=d, key=k, seconds=s)
                for u, d, k, s in self.apptime.report_entries(today)]
        if apps:
            await self.conn.send(p.AppUsageReport(entries=apps[:2000]))

    def _count_apps(self, users: set[str], day, delta: float) -> None:
        """Programmzeit: je User die Apps mit mindestens einem laufenden Prozess."""
        for user in users:
            try:
                uid = pwd.getpwnam(user).pw_uid
            except KeyError:
                continue
            keys = set()
            for pid in pids_of({uid}):
                info = read_proc(pid)
                if info is not None and (k := self.app_index.key_for(info, f"{STEAM_ROOT}/{user}/")):
                    keys.add(k)
            self.apptime.add(user, day, keys, delta)

    def _collect_blocked_domains(self) -> None:
        for cid, domain, source in self.dns.collect_blocked():
            users = sorted(self.users_of(cid))
            if users:
                self.programs.record_domain(users[0], domain, source)

    # ---------- Server-Ereignisse ----------

    async def on_connected(self, conn: Connection) -> None:
        await conn.send(p.ReportUsers(users=local_users()))
        await conn.send(p.ReportApps(apps=self.apps))
        await self.send_usage()
        await self.flush_outbox()
        await self.programs.flush(conn)
        self._integrity = None  # nach Verbindungsaufbau immer melden
        await self.check_integrity()

    async def flush_outbox(self) -> None:
        """Gepufferte Nachrichten/Anfragen senden; der Server ist idempotent (client_msg_id)."""
        if not self.online:
            return
        for item in list(self.outbox.items.values()):
            await self.conn.send(item)

    async def sync_chat(self) -> None:
        """Verlauf je Kind ab der höchsten bekannten Nachricht nachholen."""
        if not (self.online and self.policy):
            return
        for child_id in sorted(set(self.policy.users.values())):
            users = self.users_of(child_id)
            await self.conn.send(p.ChatSync(username=users[0], after_id=self.chat.max_id(child_id)))

    async def on_message(self, msg) -> None:
        if isinstance(msg, p.Policy):
            self.policy = msg
            self._synced = True
            self.programs.policy_changed()
            await self.sync_mounts()
            await self.sync_dns()
            store.save(store.POLICY_FILE, msg.model_dump(mode="json"))
            log.info("Policy %s übernommen (%d zugeordnete User)", msg.version, len(msg.users))
            await self.conn.send(p.PolicyAck(version=msg.version))
            await self.sync_chat()
        elif isinstance(msg, p.Ack):
            self.outbox.remove(msg.client_msg_id)
            if not msg.ok:
                log.warning("Server verwirft Nachricht %s: %s", msg.client_msg_id, msg.error)
        elif isinstance(msg, p.ChatMessage):
            await self.on_chat_message(msg)
        elif isinstance(msg, p.UsageUpdate):
            self.usage.others[(msg.child_id, msg.day.isoformat())] = msg.others_seconds
        elif isinstance(msg, p.Ping):
            await self.conn.send(p.Pong())

    # ---------- Chat ----------

    async def on_chat_message(self, msg: p.ChatMessage) -> None:
        had_history = self.chat.has_history(msg.child_id)
        prev = self.chat.previous(msg)
        change = self.chat.upsert(msg)
        if change is None:
            return
        self.chat.save()
        for user in self.users_of(msg.child_id):
            await self.tray.send_to_user(user, t.TrayChatMessage(message=msg))
        if had_history:  # beim allerersten Abgleich keine Flut alter Benachrichtigungen
            await self._notify_chat(msg, prev, change)

    async def _notify_chat(self, msg: p.ChatMessage, prev: p.ChatMessage | None, change: str) -> None:
        if change == NEW and not msg.from_child:
            title, body = f"Nachricht von {msg.sender_name}", msg.body
        elif (msg.kind == "extension_request" and prev is not None and prev.status == "pending"
              and msg.status in ("approved", "rejected")):
            if msg.status == "approved":
                parts = []
                if msg.approved_minutes:
                    parts.append(f"+{msg.approved_minutes} Minuten")
                if msg.approved_window_end:
                    parts.append(f"bis {msg.approved_window_end:%H:%M} Uhr")
                title, body = "Verlängerung genehmigt", " und ".join(parts) or "Genehmigt."
            else:
                title, body = "Verlängerung abgelehnt", f"{msg.sender_name} hat die Anfrage abgelehnt."
        else:
            return
        users = set(self.users_of(msg.child_id))
        for s in self._sessions:
            if s.is_user_session and s.user in users:
                await self.desktop.notify(s.user, s.uid, title, body)
                users.discard(s.user)  # je User nur einmal

    async def open_request_dialog(self, user: str) -> None:
        """Klick auf „Verlängerung anfordern“ in einer Warnung."""
        if not await self.tray.send_to_user(user, t.TrayOpenRequestDialog()):
            log.info("Verlängerung angefordert, aber keine Tray-App von %s verbunden", user)

    # ---------- Tray-Schnittstelle (für TrayServer) ----------

    def tray_state(self, user: str) -> t.TrayState:
        now = self.now_local()
        d = self.decisions(now).get(user)
        if d is None:
            return t.TrayState(restricted=False, online=self.online)
        child_id = self.policy.users[user]
        child = next(c for c in self.policy.children if c.id == child_id)
        quota, start, end = day_limits(child, now.date())
        same = self.users_of(child_id)
        return t.TrayState(
            restricted=True, child_name=child.name, allowed=d.allowed,
            remaining_seconds=d.remaining_seconds, reason=d.reason,
            used_seconds=self.usage.used_seconds(child_id, same, now.date()),
            allowed_minutes=quota // 60, window_start=start, window_end=end,
            online=self.online, pending_outbox=self.outbox.pending_for(same))

    def tray_history(self, user: str) -> list[p.ChatMessage]:
        return self.chat.history(self.policy.users[user]) if self.policy and user in self.policy.users else []

    async def tray_chat_send(self, user: str, body: str) -> bool:
        self.outbox.add(p.ChatSend(client_msg_id=new_client_msg_id(), username=user, body=body.strip()))
        await self.flush_outbox()
        return self.online

    async def tray_request(self, user: str, req: t.TrayExtensionRequest) -> bool:
        self.outbox.add(p.ExtensionRequest(
            client_msg_id=new_client_msg_id(), username=user, day=self.now_local().date(),
            minutes=req.minutes, window_end=req.window_end, reason=req.reason.strip()))
        await self.flush_outbox()
        return self.online

    # ---------- Lauf ----------

    async def run(self) -> None:
        if self.conn is None:
            log.warning("Gerät nicht gekoppelt – nur gecachte Policy wird durchgesetzt")
        if self.policy is None:
            log.warning("Keine Policy vorhanden – niemand wird eingeschränkt")
        self.refresh_apps()
        await self.programs.refresh_base(self.app_exes())
        await self.sync_mounts()  # vor der Bereit-Meldung -> vor allen Logins
        await self.sync_dns()
        self.procevents.start()
        try:
            await self.tray.start()
        except OSError:
            log.exception("Tray-Socket nicht verfügbar – Durchsetzung läuft ohne Tray weiter")
        tasks = [asyncio.create_task(self._tick_loop())]
        if self.conn is not None:
            tasks += [asyncio.create_task(self.conn.run_forever()),
                      asyncio.create_task(self._report_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await self.tray.stop()
            self.procevents.stop()
            self.usage.save()
            self.apptime.save()
