"""Steuerung: Takt (Zählen + Durchsetzen), Policy-Cache, Server-Synchronisation, Tray/Chat."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from kidctl_common import protocol as p
from kidctl_common import tray as t

from . import desktoptrust, integrity, kidmounts, sdnotify, store
from .accounting import Usage, boottime
from .apps import scan_apps
from .apptime import AppIndex, AppTime
from .chat import NEW, ChatStore, Outbox, new_client_msg_id
from .connection import Connection
from .dnsfilter import DnsFilter
from .enforcer import Enforcer
from kidctl_common import i18n
from kidctl_common.i18n import _

from .gamelimit import GameLimit
from .games import GameNames, game_id
from .kidmounts import STEAM_ROOT, KidMounts, safe_home
from .lang import UserLanguages
from .loginlock import LoginLock, is_admin
from .procs import ProcEvents, pids_of, read_environ, read_proc
from .programs import LAUNCHERS, STEAM_KEY, STEAM_SYSTEM
from .rules import Decision, day_limits, evaluate, game_limit
from .trayserver import TrayServer
from .watcher import ProgramWatcher

log = logging.getLogger("kidctl.agent")

TICK = 5.0            # Sekunden
SAVE_EVERY = 6        # Takte (= 30 s)
DESKTOP_EVERY = 2     # Takte (= 10 s): Desktop-Verknüpfungen erlaubter Programme vertrauen
REPORT_EVERY = 60.0   # Sekunden
APPS_EVERY = 6 * 3600.0  # installierte Anwendungen neu einlesen
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


def _is_launcher(key: str) -> bool:
    """Spiele-Launcher (Steam, Heroic, Lutris) zählen nicht als Spiel – nur die gestarteten Spiele."""
    exe = key[4:] if key.startswith("exe:") else ""
    return key == STEAM_KEY or exe.startswith(STEAM_SYSTEM) or exe in LAUNCHERS


def local_users() -> list[p.LocalUser]:
    return [p.LocalUser(username=u.pw_name, uid=u.pw_uid, full_name=u.pw_gecos.split(",")[0][:255])
            for u in pwd.getpwall()
            if 1000 <= u.pw_uid < 60000 and re.fullmatch(p.USERNAME_PATTERN, u.pw_name)]


class Agent:
    def __init__(self, logind, desktop, device: dict | None = None, tray_path: str | None = None) -> None:
        self.logind = logind
        self.desktop = desktop
        self.enforcer = Enforcer(logind, desktop, on_request=self.open_request_dialog, lang_of=self.lang_of)
        self.usage = Usage.load()
        self.apptime = AppTime.load()
        self.app_index = AppIndex(set())
        self.game_names = GameNames()
        self._game_of: dict[tuple[int, int], str | None] = {}  # Prozess -> Spiel-Schlüssel (Cache)
        self.playing: dict[str, dict[str, str]] = {}  # User -> {Spiel-Schlüssel: Name} („spielt gerade“)
        self.chat = ChatStore.load()
        self.outbox = Outbox.load()
        self.tray = TrayServer(self, tray_path)
        self.langs = UserLanguages()   # Sprache der Meldungen je Kind (Plasma-Sitzung)
        self.programs = ProgramWatcher(self)
        self.gamelimit = GameLimit(desktop, on_request=self.open_game_request_dialog, lang_of=self.lang_of)
        self.game_keys: set[str] = set()   # Katalog-Apps der Kategorie „Game“ (ohne Launcher)
        self.gaming: set[str] = set()      # User mit laufendem Spiel (letzter gezählter Takt)
        self._day = None  # zuletzt gesehener lokaler Tag (Ablauf der Freigaben „nur heute“)
        self.mounts = KidMounts()
        self.dns = DnsFilter()
        self.device = device
        self._integrity: list[p.IntegrityCheck] | None = None  # zuletzt gemeldet
        self.procevents = ProcEvents(self.programs.on_exec, self.programs.on_fork)
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
        await self._day_changed(now.date())
        # Login-Sperre sofort (auch mit gecachter Policy) – sonst wäre direkt nach dem Booten
        # kurz eine Anmeldung möglich. Gilt auch ohne laufende Sitzung.
        self.loginlock.update({u for u, d in decisions.items() if not d.allowed})
        if self.mounts.releasable():  # aufgeschobenes Aushängen nach der Abmeldung nachholen
            await self.sync_mounts()
        if self._ticks % DESKTOP_EVERY == 0:
            await asyncio.to_thread(self.trust_desktops)
        if self._synced or boottime() - self._started >= STARTUP_SYNC:
            await self.enforcer.step(now.date(), sessions, decisions)
            await self._enforce_games(now.date(), sessions, decisions)
            await self.programs.scan()
        await self.tray.push_states(boottime())
        self._ticks += 1
        if self._ticks % SAVE_EVERY == 0:
            self.usage.prune(now.date())
            self.usage.save()
            self.apptime.prune(now.date())
            self.apptime.save()

    def is_game(self, key: str) -> bool:
        return key.startswith("game:") or key in self.game_keys

    def game_remaining(self, user: str, day) -> int | None:
        """Restspielzeit (Sekunden) heute, ``None`` = kein Spiele-Limit."""
        child_id = self.policy.users[user]
        child = next(c for c in self.policy.children if c.id == child_id)
        limit = game_limit(child, day)
        if limit is None:
            return None
        return limit - self.usage.used_game_seconds(child_id, self.users_of(child_id), day)

    async def _enforce_games(self, day, sessions, decisions: dict[str, Decision]) -> None:
        """Spiele-Limit je User mit Sitzung, solange Bildschirmzeit bleibt (sonst regelt der Enforcer)."""
        for s in sessions:
            if not (s.is_user_session and s.user in decisions and decisions[s.user].allowed):
                continue
            remaining = self.game_remaining(s.user, day)
            procs = self._game_processes(s.uid, s.user) if remaining is not None and remaining <= 0 else []
            await self.gamelimit.step(s.user, s.uid, day, remaining, s.user in self.gaming, procs)

    def _game_processes(self, uid: int, user: str) -> list[tuple[int, int]]:
        steam_lib = f"{STEAM_ROOT}/{user}/"
        out = []
        for pid in pids_of({uid}):
            info = read_proc(pid)
            if info is None:
                continue
            key = self._game_key(info, user) or self.app_index.key_for(info, steam_lib)
            if key and self.is_game(key):
                out.append((info.pid, info.start))
        return out

    async def _day_changed(self, today) -> None:
        """Freigaben „nur heute“ laufen um Mitternacht ab (auch offline): Programme neu prüfen
        (laufende werden beendet), Spielordner und Website-Filter angleichen."""
        last, self._day = self._day, today
        if last is None or last == today or self.policy is None:
            return
        if any(g.day in (last, today) for c in self.policy.children for g in c.temp_grants):
            log.info("Tageswechsel: Freigaben „nur heute“ neu angewendet")
            self.programs.policy_changed()
            await self.sync_mounts()
            await self.sync_dns()

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
                await self.programs.refresh_base(self.app_exes(), self.policy_exes())
                self.refresh_index()  # Paket-Ordner
                if self.online:
                    await self.conn.send(p.ReportApps(apps=self.apps))

    def refresh_apps(self) -> None:
        try:
            self.apps = scan_apps()
        except OSError:
            log.exception("Anwendungen nicht lesbar")
            return
        self.programs.names = {a.key: a.name for a in self.apps}
        self.programs.names_en = {a.key: a.name_en for a in self.apps if a.name_en}
        self.game_keys = {a.key for a in self.apps if "Game" in a.categories and not _is_launcher(a.key)}
        self.refresh_index()

    def refresh_index(self) -> None:
        # Shells nicht als Programmzeit zählen (Systemprozesse wie „sh -c …“ verfälschen sonst die Statistik)
        self.app_index = AppIndex({a.key for a in self.apps if "Shell" not in a.categories},
                                  self.programs.pkg_dirs)

    def trust_desktops(self) -> None:
        """Für Kinder mit Programmkontrolle (noexec-Home): Desktop-Verknüpfungen erlaubter Programme
        root übergeben, damit KDE sie startet (siehe desktoptrust)."""
        for uid, (user, _mode, allowed) in self.programs.targets().items():
            home = safe_home(user)
            if home is None:
                continue
            try:
                desktoptrust.trust_desktop(home, uid, lambda t, a=allowed: desktoptrust.target_allowed(t, a))
            except Exception:
                log.exception("Desktop-Verknüpfungen von %s nicht prüfbar", user)

    async def sync_mounts(self) -> None:
        """noexec-Homes und Spiele-Bibliotheken an die Policy angleichen (systemd, im Thread)."""
        users = self.programs.users()
        kids = set(self.decisions(self.now_local()))  # alle zugeordneten Nicht-Admins
        try:
            await asyncio.to_thread(self.mounts.sync, users)
            await asyncio.to_thread(self.mounts.ensure_libraries, kids)
            await asyncio.to_thread(self.mounts.sync_libraries, self.locked_libraries())
        except Exception:
            log.exception("Mounts nicht angleichbar")

    def locked_libraries(self) -> set[str]:
        """Spieleordner von Kindern mit Programmkontrolle, deren Launcher nicht freigegeben ist
        (werden noexec eingehängt, siehe kidmounts)."""
        out = set()
        for user, _mode, allowed in self.programs.targets().values():
            for lib in kidmounts.libraries():
                enabled = allowed.steam_enabled if lib.key == "steam" else lib.exe in allowed.exes
                if lib.installed.exists() and not enabled and safe_home(user) is not None:
                    out.add(str(lib.root / user))
        return out

    async def sync_dns(self) -> None:
        """Website-Filter an die Policy angleichen: Listen holen, Resolver + nftables (im Thread)."""
        if self.policy is None:
            return
        eligible = self.decisions(self.now_local())  # zugeordnete Nicht-Admins
        today = self.now_local().date()
        children = {c.id: (c.web_on(today), {u for u in self.users_of(c.id) if u in eligible})
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
                + integrity.check_noexec(self.programs.users(), libraries=self.locked_libraries())
                + integrity.check_libraries(self.mounts.lib_problems)
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

    def policy_exes(self) -> set[str]:
        """Von den Eltern freigegebene Programme (auch eigene, nicht im Katalog)."""
        if self.policy is None:
            return set()
        today = self.now_local().date()
        return {k[4:] for c in self.policy.children for k in c.programs_on(today) if k.startswith("exe:")}

    def app_exes(self) -> set[str]:
        return {a.key[4:] for a in self.apps if a.key.startswith("exe:")}

    async def send_usage(self) -> None:
        if self.conn is None or not self.conn.connected:
            return
        today = self.now_local().date()
        entries = [p.UsageEntry(username=u, day=d, seconds=s, game_seconds=g)
                   for u, d, s, g in self.usage.report_entries(today)]
        if entries:
            await self.conn.send(p.UsageReport(entries=entries))
        apps = [p.AppUsageEntry(username=u, day=d, key=k, seconds=s, name=self.apptime.names.get(k, ""))
                for u, d, k, s in self.apptime.report_entries(today)]
        playing = [p.PlayingEntry(username=u, key=k, name=n)
                   for u, games in sorted(self.playing.items()) for k, n in sorted(games.items())]
        # immer senden (auch leer), damit „spielt gerade“ beim Server endet
        await self.conn.send(p.AppUsageReport(entries=apps[:2000], playing=playing[:200]))

    def _count_apps(self, users: set[str], day, delta: float) -> None:
        """Programmzeit: je User die Apps mit mindestens einem laufenden Prozess. Prozesse eines
        Steam-/Heroic-Spiels zählen für das Spiel (statt für Steam), außerdem „spielt gerade“."""
        alive: set[tuple[int, int]] = set()
        playing: dict[str, dict[str, str]] = {}
        gaming: set[str] = set()
        for user in users:
            try:
                uid = pwd.getpwnam(user).pw_uid
            except KeyError:
                continue
            steam_lib = f"{STEAM_ROOT}/{user}"
            keys = set()
            for pid in pids_of({uid}):
                info = read_proc(pid)
                if info is None:
                    continue
                alive.add((info.pid, info.start))
                if game := self._game_key(info, user):
                    keys.add(game)
                    if game not in self.apptime.names:
                        home = safe_home(user) or ""
                        self.apptime.names[game] = self.game_names.name(game, home, steam_lib)
                    playing.setdefault(user, {})[game] = self.apptime.names[game]
                elif k := self.app_index.key_for(info, steam_lib + "/"):
                    keys.add(k)
            self.apptime.add(user, day, keys, delta)
            if any(self.is_game(k) for k in keys):
                self.usage.add_games(user, day, delta)
                gaming.add(user)
        self._game_of = {k: v for k, v in self._game_of.items() if k in alive}
        self.playing = playing
        self.gaming = gaming

    def _game_key(self, info, user: str) -> str | None:
        """Spiel eines Prozesses (Umgebung nur bei Kandidaten lesen: Spieleordner, Wine, Container)."""
        ident = (info.pid, info.start)
        if ident in self._game_of:
            return self._game_of[ident]
        roots = tuple(f"{lib.root}/{user}/" for lib in kidmounts.libraries())
        candidate = (info.exe.startswith(roots) or info.mnt_foreign
                     or os.path.basename(info.exe).startswith(("wine", "pv-", "proton")))
        found = game_id(read_environ(info.pid)) if candidate else None
        key = found[0] if found else None
        self._game_of[ident] = key
        return key

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
            if self.programs.pkg_dirs_outdated(self.app_exes() | self.policy_exes()):
                await self.programs.refresh_base(self.app_exes(), self.policy_exes())  # neues eigenes Programm
                self.refresh_index()
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
            self.usage.others_games[(msg.child_id, msg.day.isoformat())] = msg.others_game_seconds
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

    def _blocked_items(self, user: str) -> dict[str, t.BlockedItem]:
        """Kürzlich Gesperrtes aller Linux-User desselben Kindes (Websites laufen unter einem User),
        mit Status „schon angefragt“."""
        child_id = self.policy.users[user]
        out: dict[str, t.BlockedItem] = {}
        for u in self.users_of(child_id):
            for item in self.programs.recent_items(u):
                if item.key not in out or item.last_at > out[item.key].last_at:
                    out[item.key] = item
        pending = {m.access_key for m in self.chat.history(child_id)
                   if m.kind == "access_request" and m.status == "pending"}
        pending |= {i.key for i in self.outbox.items.values()
                    if isinstance(i, p.AccessRequest) and self.policy.users.get(i.username) == child_id}
        with i18n.use(self.lang_of(user)):   # Programmnamen in der Sprache des Kindes
            return {k: i.model_copy(update={"pending": k in pending, "name": self.programs.display_name(k, i.name)})
                    for k, i in out.items()}

    def tray_blocked(self, user: str) -> t.TrayBlocked:
        items = sorted(self._blocked_items(user).values(), key=lambda i: i.last_at, reverse=True)
        return t.TrayBlocked(items=items)

    async def open_access_dialog(self, user: str, key: str) -> None:
        """Klick auf „Freigabe anfragen“ in der Sperr-Benachrichtigung."""
        if self.policy is None or user not in self.policy.users:
            return
        item = self._blocked_items(user).get(key)
        if item is None or not await self.tray.send_to_user(user, t.TrayOpenAccessDialog(item=item)):
            log.info("Freigabe angefragt, aber keine Tray-App von %s verbunden", user)

    async def tray_access(self, user: str, key: str, reason: str) -> tuple[bool, str]:
        """Freigabe anfragen (nur für kürzlich Gesperrtes; Name/Art aus der eigenen Liste).
        Rückgabe (online, Fehlertext)."""
        item = self._blocked_items(user).get(key)
        with i18n.use(self.lang_of(user)):
            if item is None:
                return self.online, _("Dieser Eintrag ist nicht (mehr) in der Liste der Sperren.")
            if item.pending:
                return self.online, _("Schon angefragt – warte auf die Antwort deiner Eltern.")
        self.outbox.add(p.AccessRequest(client_msg_id=new_client_msg_id(), username=user,
                                        access_kind=item.access_kind, key=item.key, name=item.name[:255],
                                        reason=reason.strip()))
        await self.flush_outbox()
        return self.online, ""

    def lang_of(self, user: str) -> str:
        return self.langs.lang_of(user)

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
            online=self.online, pending_outbox=self.outbox.pending_for(same),
            game_limit_minutes=None if (gl := game_limit(child, now.date())) is None else gl // 60,
            game_used_seconds=self.usage.used_game_seconds(child_id, same, now.date()))

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

    # ---------- Lauf ----------

    async def run(self) -> None:
        if self.conn is None:
            log.warning("Gerät nicht gekoppelt – nur gecachte Policy wird durchgesetzt")
        if self.policy is None:
            log.warning("Keine Policy vorhanden – niemand wird eingeschränkt")
        self.refresh_apps()
        await self.programs.refresh_base(self.app_exes(), self.policy_exes())
        self.refresh_index()  # Paket-Ordner
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
