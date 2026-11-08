"""Windows-Agent (Schritt 1): Bildschirmzeit, Abmeldung, Login-Sperre, „Jetzt sperren“, Tray/Chat,
Integrität und Selbst-Update – auf dem gemeinsamen Kern (``core.AgentCore``)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from kidctl_common import i18n
from kidctl_common import protocol as p
from kidctl_common.i18n import _

from .. import __version__, store
from ..accounting import boottime
from ..core import AgentCore
from ..rules import Decision
from . import integrity
from .accounts import Accounts
from .acl import AclManager
from .desktop import WinDesktop
from .langs import WinLanguages
from .loginlock import WinLoginLock
from .pipeserver import PipeTrayServer
from .sessions import WinSessions
from .update import Updater
from .dnsfilter import WinDnsFilter
from .watcher import WinProgramWatcher
from ..apptime import AppTime
from ..gamelimit import GameLimit
from .apptime import WinAppIndex

log = logging.getLogger("kidctl.agent")

LAUNCHER_PFNS = ("microsoft.gamingapp_", "microsoft.xboxgamingoverlay_", "microsoft.xboxapp_")


def is_launcher(key: str) -> bool:
    from .games import LAUNCHERS
    low = key.lower()
    return (low.startswith("appx:") and low[5:].startswith(LAUNCHER_PFNS)) or \
        (low.startswith("exe:") and low.rsplit("\\", 1)[-1] in LAUNCHERS)


BLOCKED_LOGIN_GRACE = 10   # Sekunden: Sitzung trotz Login-Sperre -> Hinweis, dann Abmeldung
TRAY_GRACE = 60            # Sekunden nach Erscheinen einer Sitzung: Zeit für den Autostart der Tray-App
TRAY_RETRY = 300           # frühestens so oft je Sitzung neu starten
SCAN_EVERY = 2             # Takte (10 s): vollständiger Abgleich aller Prozesse (neue prüft der Watcher sofort)
DNS_CHECK_EVERY = 6        # Takte (30 s): Resolver läuft? Adapter (z. B. neues VPN) noch auf dem Filter?


def install_dir() -> str:
    """Installationsordner (``…\\KidCTL``, über dem eingebetteten Python); leer außerhalb der Installation."""
    if sys.platform != "win32":
        return ""
    folder = Path(sys.executable).parent.parent
    return str(folder) if (folder / "lib" / "kidctl_agent").is_dir() else ""


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
        self.programs = WinProgramWatcher(self, install_dir=install_dir())
        self.acl = AclManager(store)
        self.dns = WinDnsFilter(install_dir())
        self.apptime = AppTime.load()
        self.playing: dict[str, dict[str, str]] = {}   # User -> {Spiel-Schlüssel: Name} („spielt gerade“)
        self.gaming: set[str] = set()                   # User mit laufendem Spiel (letzter gezählter Takt)
        self._game_procs: dict[str, list[tuple[int, int]]] = {}   # User -> Spielprozesse (PID, Start)
        self.gamelimit = GameLimit(desktop, kill=lambda pid, sig: self.programs.procs.terminate(pid),
                                   on_request=self.open_game_request_dialog, lang_of=self.lang_of)
        self.wfp = None            # WFP-Sitzung (nur Windows, beim Start geöffnet)
        self._dns_key = None       # zuletzt angewendetes Profil (aktive Sitzung, Policy, Tag)
        self._dns_lock = asyncio.Lock()   # Abgleich nie doppelt gleichzeitig (Takt, Policy, Start)
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

    async def _after_count(self, now, counting: set[str], delta: float) -> None:
        await self._count_apps(counting, now.date(), delta)
        for cid, domain, source in self.dns.collect_blocked():
            users = sorted(self.users_of(cid))
            if users:
                self.programs.record_domain(users[0], domain, source)

    async def _maintain(self) -> None:
        if self._dns_profile_key() != self._dns_key or self._ticks % DNS_CHECK_EVERY == 0:
            await self.sync_dns()
        if self.loginlock.changed:
            self.loginlock.changed = False
            await self._refresh_login_screens()
        await self._blocked_logins()

    async def _refresh_login_screens(self) -> None:
        """Der Anmeldebildschirm merkt sich die Kontenliste (ein deaktiviertes Konto fehlt, ein wieder aktiviertes
        erschiene in einer gesperrten Sitzung gar nicht). Neustart von LogonUI aktualisiert sie sofort – in Sitzungen
        ohne angemeldeten Benutzer und in gesperrten (Windows startet LogonUI neu, die Sitzung bleibt gesperrt)."""
        if self.logind is None:
            return
        try:
            for sid in await asyncio.to_thread(self.logind.login_screens):
                await asyncio.to_thread(self.accounts.api.kill_in_session, "LogonUI.exe", sid)
                log.info("Anmeldebildschirm in Sitzung %s aktualisiert", sid)
        except Exception:
            log.exception("Anmeldebildschirm nicht aktualisierbar")

    async def _blocked_logins(self) -> None:
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
        await self._ensure_trays(sessions, decisions)
        for user in {s.user for s in sessions if s.is_user_session and s.user in decisions and decisions[s.user].allowed}:
            remaining = self.game_remaining(user, day)
            procs = self._game_procs.get(user, []) if remaining is not None and remaining <= 0 else []
            await self.gamelimit.step(user, 0, day, remaining, user in self.gaming, procs)
        if self._ticks % SCAN_EVERY == 0:
            await self.programs.scan()

    # ---------- Programmkontrolle ----------

    async def _startup(self) -> None:
        if sys.platform == "win32":
            from .wfp import Wfp
            self.wfp = Wfp()
        await self.sync_dns()
        await self.programs.refresh_catalog()
        self.apps = self.programs.apps
        await self.sync_acl()
        task = asyncio.create_task(self.programs.poll_forever())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _policy_applied(self) -> None:
        self.programs.policy_changed()
        await self.sync_acl()
        self._dns_key = None
        await self.sync_dns()
        await self.programs.scan()   # neue Sperren sofort, nicht erst beim nächsten Abgleich

    def acl_targets(self) -> dict:
        """Kinder mit Ausführverbot: nur im Modus „Sperren“ („Nur protokollieren“ sperrt nichts)."""
        return {sid: target for sid, target in self.programs.targets().items() if target[1] == "enforce"}

    def acl_wanted(self) -> dict[str, list[str]]:
        """SID -> freigegebene App-Ordner der Kinder mit Ausführverbot."""
        return {sid: list(allowed.dirs) for sid, (_u, _m, allowed) in self.acl_targets().items()}

    def acl_extra(self) -> dict[str, list[str]]:
        """Steam-Bibliotheken (vom Kind beschreibbar) mit Verbot, solange Steam für das Kind nicht freigegeben ist."""
        games = self.programs.games
        if not games.steam_exe:
            return {}
        roots = [d for d in games.steam_dirs if os.path.isdir(d)]
        return {sid: roots for sid, (_u, _m, allowed) in self.acl_targets().items() if not allowed.launcher_dirs
                or games.steam_dirs[0] not in allowed.launcher_dirs}

    async def sync_acl(self) -> None:
        try:
            await asyncio.to_thread(self.acl.sync, self.acl_wanted(), self.acl_extra())
        except Exception:
            log.exception("Ausführverbote nicht anwendbar")

    # ---------- Programm- und Spielzeit ----------

    @property
    def game_keys(self) -> set[str]:
        """Katalog-Apps der Kategorie „Spiele“ ohne Launcher (Steam, Xbox-App, Game Bar …) – wie unter Linux."""
        cat = getattr(self, "programs", None) and self.programs.catalog
        if not cat:
            return set()
        return {a.key for a in cat.apps if "Game" in a.categories and not is_launcher(a.key)}

    @game_keys.setter
    def game_keys(self, _value) -> None:
        pass   # der Kern setzt einen Anfangswert; hier immer aus dem Katalog

    async def _count_apps(self, counting: set[str], day, delta: float) -> None:
        """Je User die Apps mit mindestens einem Prozess (Programmzeit), Spiele (Spielzeit, „spielt gerade“) und die
        Spielprozesse (Spiele-Limit). Gelesen wird auch ohne gezählte Zeit (Limit wirkt sofort)."""
        users = {a.sid: a.wire for a in self.accounts.all() if self.policy and a.wire in self.policy.users}
        if not users:
            self.playing, self.gaming, self._game_procs = {}, set(), {}
            return
        procs = self.programs.procs
        index = WinAppIndex(self.programs.catalog, self.programs.games)

        def collect():
            snap = procs.snapshot()
            return [i for pid, ppid in snap.items() if pid > 4 and (i := procs.read(pid, ppid)) and i.sid in users]
        try:
            infos = await asyncio.to_thread(collect)
        except Exception:
            log.exception("Prozesse nicht lesbar (Programmzeit)")
            return
        keys: dict[str, set[str]] = {}
        playing: dict[str, dict[str, str]] = {}
        game_procs: dict[str, list[tuple[int, int]]] = {}
        for info in infos:
            user = users[info.sid]
            found = index.key_for(info)
            if found is None:
                continue
            key, name = found
            keys.setdefault(user, set()).add(key)
            if name:
                self.apptime.names.setdefault(key, name)
            if self.is_game(key, user):
                playing.setdefault(user, {})[key] = self.apptime.names.get(key) or self.programs.names.get(key, key)
                game_procs.setdefault(user, []).append(info.ident)
        if delta:
            for user in counting:
                self.apptime.add(user, day, keys.get(user, set()), delta)
                if user in playing:
                    self.usage.add_games(user, day, delta)
        self.playing = {u: g for u, g in playing.items() if u in counting}
        self.gaming = set(self.playing)
        self._game_procs = game_procs

    def _app_usage_report(self, today) -> p.AppUsageReport:
        apps = [p.AppUsageEntry(username=u, day=d, key=k, seconds=s, name=self.apptime.names.get(k, ""))
                for u, d, k, s in self.apptime.report_entries(today)]
        playing = [p.PlayingEntry(username=u, key=k, name=n)
                   for u, games in sorted(self.playing.items()) for k, n in sorted(games.items())]
        return p.AppUsageReport(entries=apps[:2000], playing=playing[:200])

    def _save_more(self, day) -> None:
        self.apptime.prune(day)
        self.apptime.save()

    # ---------- Website-Filter ----------

    def web_kids(self) -> dict[str, tuple[str, int, p.WebFilter]]:
        """SID -> (User, Kind-ID, Filter heute) der zugeordneten Nicht-Admins mit Website-Filter."""
        if self.policy is None:
            return {}
        now = self.now_local()
        eligible = self.decisions(now)
        children = {c.id: c for c in self.policy.children}
        out = {}
        for user, cid in self.policy.users.items():
            child = children.get(cid)
            if child is None or user not in eligible:
                continue
            web = child.web_on(now.date())
            account = self.accounts.by_wire(user)
            if web.mode != "off" and account is not None:
                out[account.sid] = (user, cid, web)
        return out

    def _active_user(self) -> str | None:
        active = [s.user for s in self._sessions if s.active and s.is_user_session]
        return active[0] if len(active) == 1 else None

    def _dns_profile_key(self):
        kids = self.web_kids()
        background = sorted(s.user for s in self._sessions if s.is_user_session and not s.active)
        return (self.policy.version if self.policy else None, self.now_local().date(), self._active_user(),
                tuple(background), tuple(sorted(kids)))

    async def sync_dns(self) -> None:
        """Website-Filter an Policy und aktive Sitzung angleichen (Resolver, Adapter, WFP)."""
        async with self._dns_lock:
            key = self._dns_profile_key()
            kids = self.web_kids()
            try:
                await asyncio.to_thread(self._sync_dns, kids)
                self._dns_key = key
            except Exception:
                log.exception("Website-Filter nicht anwendbar")

    def _sync_dns(self, kids: dict[str, tuple[str, int, p.WebFilter]]) -> None:
        from ..dnsfilter import fetch_lists
        if not kids:
            if self.wfp:
                self.wfp.set_rules(set())
            self.dns.stop()
            self.dns.restore_adapters()
            return
        refs = [r for _u, _c, web in kids.values() if web.mode == "blacklist" for r in web.lists]
        fetch_lists(self.dns.lists_dir, refs, self.device)
        by_user = {user: (cid, web) for _sid, (user, cid, web) in kids.items()}
        cid, web = by_user.get(self._active_user(), (None, None))
        self.dns.apply(cid, web)
        self.dns.redirect_adapters()
        if self.wfp:
            background = {s.user for s in self._sessions if s.is_user_session and not s.active}
            rules = {(sid, port) for sid in kids for port in (53, 853)}
            rules |= {(sid, None) for sid, (user, _c, _w) in kids.items() if user in background}
            self.wfp.set_rules(rules)

    async def _shutdown(self) -> None:
        from .service import STOP_REASON
        self.apptime.save()
        self.dns.stop()
        if STOP_REASON.get("control") == 1:   # von einem Admin gestoppt (nicht Herunterfahren): DNS freigeben
            await asyncio.to_thread(self.dns.restore_adapters)
        if self.wfp:
            self.wfp.close()

    async def _connected_more(self, conn) -> None:
        await self.programs.flush(conn)

    async def _ensure_trays(self, sessions, decisions: dict[str, Decision]) -> None:
        """Tray-App in aktiven Kinder-Sitzungen starten, wenn keine verbunden ist (nach einem Update, das sie
        beendet hat, oder wenn das Kind sie geschlossen hat). Nicht nach angeforderter Abmeldung: Die Sitzung
        meldet sich noch einige Sekunden als aktiv, die Tray-App ist aber schon beendet."""
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
            if (not s.active or s.user not in decisions or s.user in connected or s.user in self.enforcer.hard
                    or now - first < TRAY_GRACE):
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
        await self.programs.flush(self.conn)
        if self.programs.catalog_due() and await self.programs.refresh_catalog():
            self.apps = self.programs.apps
            if self.online:
                await self.conn.send(p.ReportApps(apps=self.apps))
        await self.check_integrity()
        if self.online and self.updater.due():
            await asyncio.to_thread(self.updater.check)

    def _run_checks(self) -> list[p.IntegrityCheck]:
        assigned = set(self.policy.users) if self.policy else set()
        present = {a.wire for a in self.accounts.all()}
        users = {sid: user for sid, (user, _m, _a) in self.acl_targets().items()}
        return ([integrity.check_safeboot(), integrity.check_tray_autostart()]
                + integrity.check_tray(self.tray.tray_icons(), assigned)
                + integrity.check_admins(assigned & present, self.accounts.is_admin_wire)
                + integrity.check_acl(users, self.acl.problems(users))
                + integrity.check_dns(bool(self.web_kids()), self.dns.problems)
                + integrity.check_browser_policies(bool(self.web_kids())))

    async def check_integrity(self) -> None:
        try:
            checks = await asyncio.to_thread(self._run_checks)
            broken = [c for c in checks if not c.ok and c.id.startswith("acl:")]
            if broken:   # selbst gesetzt -> wiederherstellen
                log.warning("Ausführverbot fehlt (%s) – setze es neu", ", ".join(c.label for c in broken))
                users = {user: sid for sid, (user, _m, _a) in self.programs.targets().items()}
                for c in broken:
                    if (sid := users.get(c.id.split(":", 1)[1])):
                        self.acl.forget(sid)
                await self.sync_acl()
                checks = await asyncio.to_thread(self._run_checks)
        except Exception:
            log.exception("Integritätsprüfung fehlgeschlagen")
            return
        await self.report_integrity(checks)
