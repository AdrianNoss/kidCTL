"""Programm-Whitelist durchsetzen: Prozesse zugeordneter Kinder prüfen, ggf. beenden, protokollieren."""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import pwd
import signal
import subprocess
from datetime import datetime, timezone

from kidctl_common import protocol as p

from . import store
from .accounting import boottime
from .procs import pids_of, read_environ, read_proc
from .kidmounts import STEAM_ROOT
from .programs import (MAX_ANCESTORS, Allowed, Verdict, base_paths, classify, closure, group_members, load_base,
                       package_dirs, parse_pacman_qi, service_exes)

log = logging.getLogger("kidctl.programs")

BLOCKED_FILE = "blocked.json"
PACMAN_DB = "/var/lib/pacman/local"
MAX_PENDING = 500
REPORT_DEDUP = {"audit": 600.0, "enforce": 60.0, "domain": 600.0}  # gleiche Meldung höchstens so oft
NOTIFY_DEDUP = 60.0
MAX_FORKS = 65536  # gemerkte Eltern aus fork()-Events (älteste fallen heraus)
# Leere Argumentliste: meist mitten im exec() gelesen -> kurz warten und erneut lesen
EMPTY_ARGV_RETRIES = 5
EMPTY_ARGV_DELAY = 0.05  # Sekunden


class ProgramWatcher:
    def __init__(self, agent, kill=os.kill, read=read_proc, list_pids=pids_of, clock=boottime) -> None:
        self.agent = agent
        self.kill, self.read, self.list_pids, self.clock = kill, read, list_pids, clock
        self.spec = load_base()
        self.base_exact: frozenset[str] = frozenset()  # aus der Paketkette (refresh_base)
        self.pkg_dirs: dict[str, tuple[str, ...]] = {}  # App -> eigene Paket-Ordner (refresh_base)
        # Elternkette von Prozessen, die nur über ihre Abstammung erlaubt sind (Steam, Launcher,
        # Electron): Verwaiste Prozesse (z. B. wineserver) behalten sie bei erneuter Prüfung
        self.lineage: dict[tuple[int, int], tuple] = {}
        self._seen: set[tuple[int, int]] = set()  # Abstammungen, die schon einen Scan überstanden haben
        # Kind-PID -> Eltern-PID beim fork() (Netlink): Die echte Abstammung bleibt erhalten, auch wenn der
        # Elternprozess endet und das Kind an einen Subreaper (systemd --user) umgehängt wird
        self.forks: dict[int, int] = {}
        self._pacman_mtime: float | None = None
        self._pkg_input: frozenset[str] = frozenset()  # Programme, für die pkg_dirs berechnet sind
        self.names: dict[str, str] = {}             # App-Schlüssel -> Anzeigename (aus .desktop)
        self.handled: set[tuple] = set()  # bereits geprüfte Prozesse (ProcInfo.ident)
        self.last_report: dict[tuple[str, str], float] = {}
        self.last_notify: dict[tuple[str, str], float] = {}
        self.pending: list[p.BlockedEvent] = [
            p.BlockedEvent.model_validate(e) for e in (store.load(BLOCKED_FILE) or {}).get("events", [])]
        self._targets: dict[int, tuple[str, str, Allowed]] | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---------- Zielmenge aus der Policy ----------

    def policy_changed(self) -> None:
        self._targets = None
        self.handled.clear()  # Freigaben können sich geändert haben

    def targets(self) -> dict[int, tuple[str, str, Allowed]]:
        """UID -> (User, Modus, Erlaubt) für zugeordnete Nicht-Admins mit Programmkontrolle."""
        if self._targets is None:
            out = {}
            pol = self.agent.policy
            if pol is not None:
                children = {c.id: c for c in pol.children}
                eligible = self.agent.decisions(self.agent.now_local())  # ohne Admins
                for user, child_id in pol.users.items():
                    child = children.get(child_id)
                    if child is None or child.program_mode == "off" or user not in eligible:
                        continue
                    try:
                        pw = pwd.getpwnam(user)
                    except KeyError:
                        continue
                    uid, home = pw.pw_uid, pw.pw_dir
                    out[uid] = (user, child.program_mode,
                                Allowed(self.spec.globs, set(child.allowed_programs), self.base_exact,
                                        self.spec.excludes, steam_root=f"{STEAM_ROOT}/{user}/",
                                        pkg_dirs=self.pkg_dirs, home=home))
            self._targets = out
        return self._targets

    # ---------- Prüfen ----------

    async def scan(self) -> None:
        targets = self.targets()
        if not targets:
            return
        alive = set()
        for pid in self.list_pids(set(targets)):
            ident = await self.check_pid(pid)
            if ident:
                alive.add(ident)
        self.handled &= alive  # beendete Prozesse vergessen
        live = {i[:2] for i in alive}
        # Beendete erst eine Runde später vergessen: Ihre Kinder warten evtl. noch auf die Prüfung
        self.lineage = {k: v for k, v in self.lineage.items() if k in live or k not in self._seen}
        self._seen = set(self.lineage)

    def on_exec(self, pid: int) -> None:
        """Netlink-Callback (synchron) – Prüfung als Task."""
        task = asyncio.get_running_loop().create_task(self.check_pid(pid))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_fork(self, parent: int, child: int) -> None:
        """Netlink-Callback: Elternprozess merken (PID-Wiederverwendung überschreibt den Eintrag)."""
        forks = self.forks
        forks.pop(child, None)
        forks[child] = parent
        if len(forks) > MAX_FORKS:
            del forks[next(iter(forks))]

    async def check_pid(self, pid: int) -> tuple | None:
        targets = self.targets()
        if not targets:
            return None
        info = self.read(pid)
        for _ in range(EMPTY_ARGV_RETRIES):
            if info is None or info.uid not in targets or info.argv:
                break
            await asyncio.sleep(EMPTY_ARGV_DELAY)
            info = self.read(pid)
        if info is None or info.uid not in targets:
            return None
        if info.ident in self.handled:
            return info.ident
        self.handled.add(info.ident)
        user, mode, allowed = targets[info.uid]
        if mode == "enforce" and not self.base_exact:
            mode = "audit"  # Sicherung: ohne Basisliste nie beenden (würde die Sitzung zerstören)
        chain: tuple | None = None

        def ancestors():
            nonlocal chain  # einmal lesen (auch fürs Merken und die Diagnose)
            if chain is None:
                chain = tuple(itertools.islice(self._ancestors(info), MAX_ANCESTORS))
            return chain
        verdict = classify(info, allowed, ancestors=ancestors, environ=lambda: read_environ(info.pid))
        if verdict is None:
            if chain is not None:
                # Kette merken: Kurzlebige Kinder (z. B. pv-try-setlocale) finden sie auch dann noch,
                # wenn dieser Prozess bei ihrer Prüfung schon beendet ist
                self.lineage.setdefault((info.pid, info.start), chain)
            return info.ident
        name = self.names.get(verdict.key, verdict.name)
        if mode == "enforce":
            try:
                self.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self._notify(user, info.uid, verdict.key, name)
        new = self._record(user, verdict, name, audit=mode != "enforce")
        # Log gedrosselt wie das Protokoll (z. B. Firefox startet viele Prozesse)
        log.log(logging.WARNING if new else logging.DEBUG, "%s: %s (%s%s) von %s, PID %s; Eltern: %s; Aufruf: %s",
                "Gesperrt" if mode == "enforce" else "Protokolliert (würde sperren)", name, verdict.key,
                f", {verdict.detail}" if verdict.detail else "", user, pid, _describe(info, ancestors()),
                " ".join(info.argv)[:300])
        return info.ident

    async def _notify(self, user: str, uid: int, key: str, name: str) -> None:
        now = self.clock()
        if now - self.last_notify.get((user, key), -1e9) < NOTIFY_DEDUP:
            return
        self.last_notify[(user, key)] = now
        await self.agent.desktop.notify(user, uid, "Programm gesperrt",
                                        f"„{name}“ ist nicht freigegeben. Frag deine Eltern.")

    def _record(self, user: str, verdict, name: str, audit: bool, kind: str = "program") -> bool:
        """Ins Protokoll aufnehmen (gedrosselt je User + Schlüssel). True = neu aufgenommen."""
        now = self.clock()
        k = (user, verdict.key)
        dedup = REPORT_DEDUP["domain" if kind == "domain" else "audit" if audit else "enforce"]
        if now - self.last_report.get(k, -1e9) < dedup:
            return False
        self.last_report[k] = now
        self.pending.append(p.BlockedEvent(username=user, at=datetime.now(timezone.utc), kind=kind,
                                           key=verdict.key[:512], name=name[:255], detail=verdict.detail[:255],
                                           audit=audit))
        del self.pending[:-MAX_PENDING]
        self._save()
        return True

    def _save(self) -> None:
        store.save(BLOCKED_FILE, {"events": [e.model_dump(mode="json") for e in self.pending]})

    def _ancestors(self, info):
        """Elternkette; bei gemerkter Abstammung (des Prozesses oder eines Vorfahren) die damalige.
        Eltern laut fork()-Event haben Vorrang vor ``ppid`` (nach Umhängen an einen Subreaper)."""
        if (saved := self.lineage.get((info.pid, info.start))) is not None:
            yield from saved
            return
        child_start = info.start
        pid = self.forks.get(info.pid, info.ppid)
        for _ in range(2 * MAX_ANCESTORS):
            if pid <= 1:
                return
            parent = self.read(pid)
            if parent is None or parent.start > child_start:
                # schon beendet: gemerkte Kette dieses Elternprozesses (PID-Wiederverwendung ist in
                # dem kurzen Fenster ausgeschlossen – das Kind hing eben noch an ihm), sonst über
                # dessen fork()-Eltern weiter (z. B. beendete Subshell eines Skripts)
                saved = next((c for (p, _), c in self.lineage.items() if p == pid), None)
                if saved is not None:
                    yield from saved
                    return
                if pid not in self.forks:
                    return
                pid = self.forks[pid]
                continue
            yield parent
            if (saved := self.lineage.get((parent.pid, parent.start))) is not None:
                yield from saved
                return
            child_start = parent.start
            pid = self.forks.get(parent.pid, parent.ppid)

    def users(self) -> set[str]:
        """Kinder mit Programmkontrolle (für noexec-Home und Steam-Bibliothek)."""
        return {user for user, _mode, _allowed in self.targets().values()}

    def record_domain(self, user: str, domain: str, detail: str) -> bool:
        return self._record(user, Verdict(f"domain:{domain}", domain, detail), domain, audit=False, kind="domain")

    # ---------- Basisliste aus der Paketkette ----------

    def base_outdated(self) -> bool:
        try:
            return os.stat(PACMAN_DB).st_mtime != self._pacman_mtime
        except OSError:
            return False

    def pkg_dirs_outdated(self, exes: set[str]) -> bool:
        """Freigegebene Programme, deren Paket-Ordner noch nicht berechnet sind (z. B. neues eigenes
        Programm der Eltern wie /usr/bin/git -> /usr/lib/git-core)."""
        return not exes <= self._pkg_input

    async def refresh_base(self, app_exes: set[str], extra: set[str] = frozenset()) -> None:
        """Basisliste neu berechnen (pacman, im Thread). Bei Fehlern bleibt die alte erhalten.
        ``extra``: weitere freigegebene Programme (eigene Programme der Eltern) – nur für Paket-Ordner,
        nicht aus der Basisliste genommen."""
        try:
            mtime = os.stat(PACMAN_DB).st_mtime
            exact = await asyncio.to_thread(_compute_base, self.spec.packages, app_exes, self.spec.excludes,
                                            self.spec.groups)
            pkg_dirs = await asyncio.to_thread(_compute_pkg_dirs, app_exes | extra)
            self._pkg_input = frozenset(app_exes | extra)
        except (OSError, subprocess.SubprocessError) as e:
            log.error("Basisliste nicht berechenbar (%s) – Programmkontrolle protokolliert nur", e)
            return
        self.base_exact, self.pkg_dirs, self._pacman_mtime = exact, pkg_dirs, mtime
        self.policy_changed()
        log.info("Basisliste: %d Programme aus %d Wurzelpaketen und Gruppen %s", len(exact),
                 len(self.spec.packages), ", ".join(self.spec.groups) or "–")

    # ---------- Server ----------

    async def flush(self, conn) -> None:
        if not self.pending or conn is None or not conn.connected:
            return
        batch = list(self.pending)
        if await conn.send(p.BlockedReport(events=batch)):
            self.pending = self.pending[len(batch):]
            self._save()


def _describe(info, chain: tuple) -> str:
    """Elternkette fürs Log (Diagnose, z. B. abgerissene Kette bei Launchern)."""
    parts = [f"{a.pid}:{os.path.basename(a.exe)}" for a in chain[:10]]
    last = chain[-1].ppid if chain else info.ppid
    if last > 1:
        parts.append(f"{last}:<nicht lesbar>")
    return " ← ".join(parts) or "–"


def _compute_base(packages: list[str], app_exes: set[str], excludes: list[str],
                  groups: list[str] = ()) -> frozenset[str]:
    env = {"LC_ALL": "C", "PATH": "/usr/bin"}
    qi = subprocess.run(["pacman", "-Qi"], capture_output=True, text=True, env=env, check=True, timeout=60)
    deps, provides = parse_pacman_qi(qi.stdout)
    roots = list(packages) + group_members(qi.stdout, list(groups))
    pkgs = sorted(closure(deps, provides, roots))
    if not pkgs:
        return frozenset()
    ql = subprocess.run(["pacman", "-Qlq", *pkgs], capture_output=True, text=True, env=env, check=True,
                        timeout=60)
    files = [f for f in ql.stdout.split() if os.path.isfile(f) and os.access(f, os.X_OK)]
    services = service_exes()
    log.info("Basisliste: %d Programme aus Sitzungsdiensten (Autostart, D-Bus, systemd --user)", len(services))
    return base_paths(files + sorted(services), app_exes, excludes)


def _compute_pkg_dirs(app_exes: set[str]) -> dict[str, tuple[str, ...]]:
    ql = subprocess.run(["pacman", "-Ql"], capture_output=True, text=True, env={"LC_ALL": "C", "PATH": "/usr/bin"},
                        check=True, timeout=120)
    return package_dirs(ql.stdout, app_exes)
