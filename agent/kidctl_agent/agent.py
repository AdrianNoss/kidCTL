"""Linux-Agent: Kern (``core.AgentCore``) plus Programmkontrolle, Mounts, Website-Filter, Spiele, Desktop."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
import re

from kidctl_common import protocol as p
from kidctl_common import tray as t

from . import desktoptrust, gnomesearch, integrity, kidmounts, sdnotify
from .accounting import boottime
from .apps import scan_apps
from .apptime import AppIndex, AppTime
from .chat import new_client_msg_id
from .connection import Connection
from .core import STARTUP_SYNC, AgentCore  # noqa: F401 – STARTUP_SYNC: Weitergabe (Tests)
from .dnsfilter import DnsFilter
from kidctl_common import i18n
from kidctl_common.i18n import _

from .gamelimit import GameLimit
from .games import GameNames, game_id
from .gnomesearch import GnomeSearch
from .kidmounts import STEAM_ROOT, KidMounts, safe_home
from .lang import UserLanguages
from .loginlock import LoginLock, is_admin
from .procs import ProcEvents, pids_of, read_environ, read_proc
from .programs import LAUNCHERS, STEAM_KEY, STEAM_SYSTEM
from .rules import Decision, game_limit
from .trayserver import TrayServer
from .watcher import ProgramWatcher

log = logging.getLogger("kidctl.agent")

DESKTOP_EVERY = 2     # Takte (= 10 s): Desktop-Verknüpfungen erlaubter Programme vertrauen
APPS_EVERY = 6 * 3600.0  # installierte Anwendungen neu einlesen


def _is_launcher(key: str) -> bool:
    """Spiele-Launcher (Steam, Heroic, Lutris) zählen nicht als Spiel – nur die gestarteten Spiele."""
    exe = key[4:] if key.startswith("exe:") else ""
    return key == STEAM_KEY or exe.startswith(STEAM_SYSTEM) or exe in LAUNCHERS


def local_users() -> list[p.LocalUser]:
    return [p.LocalUser(username=u.pw_name, uid=u.pw_uid, full_name=u.pw_gecos.split(",")[0][:255])
            for u in pwd.getpwall()
            if 1000 <= u.pw_uid < 60000 and re.fullmatch(p.USERNAME_PATTERN, u.pw_name)]


class Agent(AgentCore):
    admin_text = "Admin (wheel/sudo)"

    def __init__(self, logind, desktop, device: dict | None = None, tray_path: str | None = None) -> None:
        super().__init__(logind, desktop, device)
        self.apptime = AppTime.load()
        self.app_index = AppIndex(set())
        self.game_names = GameNames()
        self._game_of: dict[tuple[int, int], str | None] = {}  # Prozess -> Spiel-Schlüssel (Cache)
        self.playing: dict[str, dict[str, str]] = {}  # User -> {Spiel-Schlüssel: Name} („spielt gerade“)
        self.tray = TrayServer(self, tray_path)
        self.langs = UserLanguages()   # Sprache der Meldungen je Kind (Desktop-Sitzung)
        # GNOME-Suchanbieter nicht freigegebener Apps abschalten (nur mit echtem UserSession-Objekt)
        self.gnome_search = GnomeSearch(getattr(desktop, "_as_user", lambda *a: None))
        self.programs = ProgramWatcher(self)
        self.gamelimit = GameLimit(desktop, on_request=self.open_game_request_dialog, lang_of=self.lang_of)
        self.game_keys: set[str] = set()   # Katalog-Apps der Kategorie „Game“ (ohne Launcher)
        self.gaming: set[str] = set()      # User mit laufendem Spiel (letzter gezählter Takt)
        self._day = None  # zuletzt gesehener lokaler Tag (Ablauf der Freigaben „nur heute“)
        self.mounts = KidMounts()
        self.dns = DnsFilter()
        self.procevents = ProcEvents(self.programs.on_exec, self.programs.on_fork)
        self.loginlock = LoginLock()

    # ---------- Plattform ----------

    def clock(self) -> float:
        return boottime()

    def is_admin_user(self, user: str) -> bool:
        return is_admin(user)

    def local_users(self) -> list[p.LocalUser]:
        return local_users()

    # ---------- Takt (Haken des Kerns) ----------

    async def _after_count(self, now, counting: set[str], delta: float) -> None:
        if delta:
            self._count_apps(counting, now.date(), delta)
        self._collect_blocked_domains()
        await self._day_changed(now.date())

    async def _maintain(self) -> None:
        if self.mounts.releasable():  # aufgeschobenes Aushängen nach der Abmeldung nachholen
            await self.sync_mounts()
        if self._ticks % DESKTOP_EVERY == 0:
            await asyncio.to_thread(self.trust_desktops)
            await asyncio.to_thread(self.sync_gnome_search)

    async def _enforce_more(self, day, sessions, decisions: dict[str, Decision]) -> None:
        await self._enforce_games(day, sessions, decisions)
        await self.programs.scan()

    def _save_more(self, day) -> None:
        self.apptime.prune(day)
        self.apptime.save()

    def _ready(self) -> None:
        # Erst nach dem ersten Takt (Sperrliste geschrieben) bereit melden – die Unit
        # liegt vor systemd-user-sessions.service, Anmeldungen sind also erst danach möglich.
        sdnotify.notify("READY=1")

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

    async def _report_more(self) -> None:
        await self.programs.flush(self.conn)
        await self.check_integrity()
        if boottime() - self._last_apps >= APPS_EVERY or self.programs.base_outdated():
            self._last_apps = boottime()  # nach pacman-Updates: Apps und Basisliste neu
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

    def sync_gnome_search(self) -> None:
        """GNOME-Suchanbieter nicht freigegebener Apps für Kinder im Modus „Sperren“ abschalten (siehe
        gnomesearch); sonst früher abgeschaltete wieder einschalten. Nur bei laufender GNOME-Sitzung."""
        running = getattr(self.desktop, "running_desktop", None)
        if running is None:
            return
        targets = {user: (uid, mode, allowed) for uid, (user, mode, allowed) in self.programs.targets().items()}
        users = set(targets) | set(self.gnome_search.managed)
        if not users:
            return
        found = gnomesearch.providers()
        for user in sorted(users):
            try:
                uid = targets[user][0] if user in targets else pwd.getpwnam(user).pw_uid
            except KeyError:
                continue
            if running(uid) != "gnome":
                continue
            blocked = set()
            if user in targets and targets[user][1] == "enforce":
                allowed = targets[user][2]
                blocked = {d for d, target in found.items() if not desktoptrust.target_allowed(target, allowed)}
            try:
                self.gnome_search.sync(user, uid, blocked)
            except Exception:
                log.exception("GNOME-Suche von %s nicht anpassbar", user)

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
                enabled = (allowed.steam_enabled if lib.key == "steam"
                           else lib.exe in allowed.exes or os.path.realpath(lib.exe) in allowed.exes)
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
                + integrity.check_dns(self.web_children())
                + integrity.check_desktop_packages()
                + integrity.check_gdm_autologin(set(self.policy.users) if self.policy else set())
                + integrity.check_tray(self.tray.tray_icons(), set(self.policy.users) if self.policy else set()))

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
        await self.report_integrity(checks)

    def policy_exes(self) -> set[str]:
        """Von den Eltern freigegebene Programme (auch eigene, nicht im Katalog)."""
        if self.policy is None:
            return set()
        today = self.now_local().date()
        return {k[4:] for c in self.policy.children for k in c.programs_on(today) if k.startswith("exe:")}

    def app_exes(self) -> set[str]:
        return {a.key[4:] for a in self.apps if a.key.startswith("exe:")}

    def _app_usage_report(self, today) -> p.AppUsageReport:
        apps = [p.AppUsageEntry(username=u, day=d, key=k, seconds=s, name=self.apptime.names.get(k, ""))
                for u, d, k, s in self.apptime.report_entries(today)]
        playing = [p.PlayingEntry(username=u, key=k, name=n)
                   for u, games in sorted(self.playing.items()) for k, n in sorted(games.items())]
        # immer senden (auch leer), damit „spielt gerade“ beim Server endet
        return p.AppUsageReport(entries=apps[:2000], playing=playing[:200])

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

    async def _connected_more(self, conn: Connection) -> None:
        await self.programs.flush(conn)

    async def _policy_applied(self) -> None:
        self.programs.policy_changed()
        if self.programs.pkg_dirs_outdated(self.app_exes() | self.policy_exes()):
            await self.programs.refresh_base(self.app_exes(), self.policy_exes())  # neues eigenes Programm
            self.refresh_index()
        await self.sync_mounts()
        await self.sync_dns()

    # ---------- Lauf ----------

    async def _startup(self) -> None:
        self._last_apps = boottime()
        self.refresh_apps()
        await self.programs.refresh_base(self.app_exes(), self.policy_exes())
        self.refresh_index()  # Paket-Ordner
        await self.sync_mounts()  # vor der Bereit-Meldung -> vor allen Logins
        await self.sync_dns()
        self.procevents.start()

    async def _shutdown(self) -> None:
        self.procevents.stop()
        self.apptime.save()
