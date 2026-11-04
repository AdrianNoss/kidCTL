"""Plattformneutraler Kern des Agenten: Policy-Cache, Entscheidungen, Zählung, Durchsetzung der
Bildschirmzeit, Chat/Tray-Schnittstelle und Server-Synchronisation.

Linux (``agent.Agent``) und Windows (``win.agent.WinAgent``) ergänzen ihn über die Haken ``_after_count``,
``_maintain``, ``_enforce_more``, ``_save_more``, ``_startup``, ``_report_more``, ``_ready`` und
``_policy_applied`` sowie die Plattformobjekte (Sitzungen, Desktop, Login-Sperre, Tray-Server, Sprachen).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from kidctl_common import i18n
from kidctl_common import protocol as p
from kidctl_common import tray as t
from kidctl_common.i18n import _

from . import store
from .accounting import Usage
from .chat import NEW, ChatStore, Outbox, new_client_msg_id
from .connection import Connection
from .enforcer import Enforcer
from .rules import Decision, day_limits, evaluate, game_limit

log = logging.getLogger("kidctl.agent")

TICK = 5.0            # Sekunden
SAVE_EVERY = 6        # Takte (= 30 s)
REPORT_EVERY = 60.0   # Sekunden
# Nach dem Start kurz auf die aktuelle Policy vom Server warten, bevor durchgesetzt wird –
# sonst kann die (ältere) gecachte Policy einen Fehlalarm auslösen. Gezählt wird trotzdem.
STARTUP_SYNC = 10.0   # Sekunden
DEFAULT_TZ = "Europe/Berlin"


def _chat_texts(msg: p.ChatMessage, change: str) -> tuple[str, str]:
    """(Titel, Text) der Benachrichtigung zu einer neuen Nachricht bzw. Entscheidung (aktuelle Sprache)."""
    if change == NEW and not msg.from_child:
        return _("Nachricht von {name}").format(name=msg.sender_name), msg.body
    if msg.kind == "extension_request":
        if msg.status != "approved":
            return _("Verlängerung abgelehnt"), _("{name} hat die Anfrage abgelehnt.").format(name=msg.sender_name)
        parts = []
        if msg.approved_minutes:
            parts.append(_("+{n} Minuten").format(n=msg.approved_minutes))
        if msg.approved_window_end:
            parts.append(_("bis {time} Uhr").format(time=f"{msg.approved_window_end:%H:%M}"))
        title = _("Spielzeit verlängert") if msg.request_target == "games" else _("Verlängerung genehmigt")
        return title, _(" und ").join(parts) or _("Genehmigt.")
    if msg.status != "approved":
        return _("Freigabe abgelehnt"), _("„{name}“ bleibt gesperrt.").format(name=msg.access_name)
    text = _("„{name}“ ist heute erlaubt.") if msg.access_scope == "today" else _("„{name}“ ist ab jetzt erlaubt.")
    return _("Freigabe erteilt"), text.format(name=msg.access_name)


class AgentCore:
    """Gemeinsamer Ablauf. Unterklassen setzen ``self.tray``, ``self.loginlock`` und ``self.langs``
    und liefern ``clock()``, ``is_admin_user(user)`` und ``local_users()``."""

    admin_text = "Admin"   # für die Log-Warnung bei zugeordneten Admins

    def __init__(self, logind, desktop, device: dict | None = None) -> None:
        self.logind = logind
        self.desktop = desktop
        self.enforcer = Enforcer(logind, desktop, on_request=self.open_request_dialog, lang_of=self.lang_of)
        self.usage = Usage.load()
        self.chat = ChatStore.load()
        self.outbox = Outbox.load()
        self.device = device
        self._integrity: list[p.IntegrityCheck] | None = None  # zuletzt gemeldet
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
        self._admin_warned: set[str] = set()
        self._started = self.clock()
        self._synced = self.conn is None  # ohne Server: sofort gecachte Policy durchsetzen

    # ---------- Plattform ----------

    def clock(self) -> float:
        raise NotImplementedError

    def is_admin_user(self, user: str) -> bool:
        raise NotImplementedError

    def local_users(self) -> list[p.LocalUser]:
        raise NotImplementedError

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
            if self.is_admin_user(user):  # Grundregel: Admin-Accounts nie einschränken
                if user not in self._admin_warned:
                    log.warning("%s ist %s – Zuordnung zu Kind %s wird ignoriert", user, self.admin_text, child_id)
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
        await self._after_count(now, counting, delta)
        # Login-Sperre sofort (auch mit gecachter Policy) – sonst wäre direkt nach dem Booten
        # kurz eine Anmeldung möglich. Gilt auch ohne laufende Sitzung.
        self.loginlock.update({u for u, d in decisions.items() if not d.allowed})
        await self._maintain()
        if self._synced or self.clock() - self._started >= STARTUP_SYNC:
            await self.enforcer.step(now.date(), sessions, decisions)
            await self._enforce_more(now.date(), sessions, decisions)
        await self.tray.push_states(self.clock())
        self._ticks += 1
        if self._ticks % SAVE_EVERY == 0:
            self.usage.prune(now.date())
            self.usage.save()
            self._save_more(now.date())

    async def _after_count(self, now: datetime, counting: set[str], delta: float) -> None:
        """Nach dem Zählen (Linux: Programmzeit, gesperrte Domains, Tageswechsel)."""

    async def _maintain(self) -> None:
        """Nach der Login-Sperre (Linux: Mounts, Desktop-Verknüpfungen, GNOME-Suche)."""

    async def _enforce_more(self, day, sessions, decisions: dict[str, Decision]) -> None:
        """Nach dem Enforcer (Linux: Spiele-Limit, Programmkontrolle)."""

    def _save_more(self, day) -> None:
        """Beim regelmäßigen Speichern (Linux: Programmzeit)."""

    async def _tick_loop(self) -> None:
        ready = False
        while True:
            try:
                await self.tick_once()
            except Exception:
                log.exception("Fehler im Takt")
            if not ready:
                ready = True
                self._ready()
            await asyncio.sleep(TICK)

    def _ready(self) -> None:
        """Nach dem ersten Takt (Sperrliste geschrieben)."""

    async def _report_loop(self) -> None:
        while True:
            await asyncio.sleep(REPORT_EVERY)
            await self.send_usage()
            await self._report_more()

    async def _report_more(self) -> None:
        """Jede Minute nach dem Nutzungsbericht (Integrität, Programme …)."""

    async def send_usage(self) -> None:
        if self.conn is None or not self.conn.connected:
            return
        today = self.now_local().date()
        entries = [p.UsageEntry(username=u, day=d, seconds=s, game_seconds=g)
                   for u, d, s, g in self.usage.report_entries(today)]
        if entries:
            await self.conn.send(p.UsageReport(entries=entries))
        if (apps := self._app_usage_report(today)) is not None:
            await self.conn.send(apps)

    def _app_usage_report(self, today) -> p.AppUsageReport | None:
        return None

    # ---------- Server-Ereignisse ----------

    async def on_connected(self, conn: Connection) -> None:
        await conn.send(p.ReportUsers(users=self.local_users()))
        await conn.send(p.ReportApps(apps=self.apps))
        await self.send_usage()
        await self.flush_outbox()
        await self._connected_more(conn)
        self._integrity = None  # nach Verbindungsaufbau immer melden
        await self.check_integrity()

    async def _connected_more(self, conn: Connection) -> None:
        pass

    async def check_integrity(self) -> None:
        pass

    async def report_integrity(self, checks: list[p.IntegrityCheck]) -> None:
        """Bei Änderung melden (fehlschlagende Prüfungen ins Log)."""
        if checks != self._integrity:
            for c in checks:
                if not c.ok:
                    log.error("Integrität verletzt: %s – %s", c.label, c.detail)
            if self.online and await self.conn.send(p.IntegrityReport(checks=checks)):
                self._integrity = checks

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
            await self._policy_applied()
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
            self.usage.others_games[(msg.child_id, msg.day.isoformat())] = msg.others_game_seconds
        elif isinstance(msg, p.Ping):
            await self.conn.send(p.Pong())

    async def _policy_applied(self) -> None:
        """Neue Policy übernommen, vor dem Speichern (Linux: Programme, Mounts, Website-Filter)."""

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
        decided = prev is not None and prev.status == "pending" and msg.status in ("approved", "rejected")
        if not ((change == NEW and not msg.from_child) or (decided and msg.kind in ("extension_request", "access_request"))):
            return
        users = set(self.users_of(msg.child_id))
        for s in self._sessions:
            if s.is_user_session and s.user in users:
                with i18n.use(self.lang_of(s.user)):   # Sprache der Sitzung dieses Kindes
                    title, body = _chat_texts(msg, change)
                await self.desktop.notify(s.user, s.uid, title, body)
                users.discard(s.user)  # je User nur einmal

    async def open_request_dialog(self, user: str, target: str = "screen") -> None:
        """Klick auf „Verlängerung anfordern“ in einer Warnung."""
        if not await self.tray.send_to_user(user, t.TrayOpenRequestDialog(target=target)):
            log.info("Verlängerung angefordert, aber keine Tray-App von %s verbunden", user)

    async def open_game_request_dialog(self, user: str) -> None:
        """Klick auf „Mehr Spielzeit anfragen“ in einer Spielzeit-Warnung."""
        await self.open_request_dialog(user, "games")

    def lang_of(self, user: str) -> str:
        return self.langs.lang_of(user)

    # ---------- Tray-Schnittstelle (für den Tray-Server) ----------

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
            online=self.online, pending_outbox=self.outbox.pending_for(same),
            game_limit_minutes=None if (gl := game_limit(child, now.date())) is None else gl // 60,
            game_used_seconds=self.usage.used_game_seconds(child_id, same, now.date()),
            logout_in_seconds=self.logout_in(user))

    def logout_in(self, user: str) -> int | None:
        """Sekunden bis zur Abmeldung (laufender Countdown), sonst ``None``."""
        deadline = self.enforcer.deadline.get(user)
        return None if deadline is None else max(0, round(deadline - self.enforcer.clock()))

    async def tray_action(self, user: str, action_id: str) -> None:
        """Klick auf den Knopf einer Benachrichtigung, die die Tray-App angezeigt hat (Windows)."""
        handler = getattr(self.desktop, "action", None)
        if handler is not None:
            await handler(user, action_id)

    def tray_history(self, user: str) -> list[p.ChatMessage]:
        return self.chat.history(self.policy.users[user]) if self.policy and user in self.policy.users else []

    async def tray_chat_send(self, user: str, body: str) -> bool:
        self.outbox.add(p.ChatSend(client_msg_id=new_client_msg_id(), username=user, body=body.strip()))
        await self.flush_outbox()
        return self.online

    async def tray_request(self, user: str, req: t.TrayExtensionRequest) -> bool:
        self.outbox.add(p.ExtensionRequest(
            client_msg_id=new_client_msg_id(), username=user, day=self.now_local().date(),
            minutes=req.minutes, window_end=None if req.target == "games" else req.window_end,
            reason=req.reason.strip(), target=req.target))
        await self.flush_outbox()
        return self.online

    def tray_blocked(self, user: str) -> t.TrayBlocked:
        return t.TrayBlocked(items=[])

    async def tray_access(self, user: str, key: str, reason: str) -> tuple[bool, str]:
        with i18n.use(self.lang_of(user)):
            return self.online, _("Dieser Eintrag ist nicht (mehr) in der Liste der Sperren.")

    async def open_access_dialog(self, user: str, key: str) -> None:
        pass

    # ---------- Lauf ----------

    async def _startup(self) -> None:
        """Vor dem ersten Takt (Linux: Apps, Basisliste, Mounts, Website-Filter, Prozess-Events)."""

    async def _shutdown(self) -> None:
        pass

    async def run(self) -> None:
        if self.conn is None:
            log.warning("Gerät nicht gekoppelt – nur gecachte Policy wird durchgesetzt")
        if self.policy is None:
            log.warning("Keine Policy vorhanden – niemand wird eingeschränkt")
        await self._startup()
        try:
            await self.tray.start()
        except OSError:
            log.exception("Tray-Verbindung nicht verfügbar – Durchsetzung läuft ohne Tray weiter")
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
            await self._shutdown()
            self.usage.save()
