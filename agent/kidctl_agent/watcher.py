"""Programm-Whitelist durchsetzen: Prozesse zugeordneter Kinder prüfen, ggf. beenden, protokollieren."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
import signal
import subprocess
from datetime import datetime, timezone

from kidctl_common import protocol as p

from . import store
from .accounting import boottime
from .procs import pids_of, read_proc
from .programs import Allowed, base_paths, classify, closure, load_base, parse_pacman_qi

log = logging.getLogger("kidctl.programs")

BLOCKED_FILE = "blocked.json"
PACMAN_DB = "/var/lib/pacman/local"
MAX_PENDING = 500
REPORT_DEDUP = {"audit": 600.0, "enforce": 60.0}  # gleiche (User, Programm)-Meldung höchstens so oft
NOTIFY_DEDUP = 60.0


class ProgramWatcher:
    def __init__(self, agent, kill=os.kill, read=read_proc, list_pids=pids_of, clock=boottime) -> None:
        self.agent = agent
        self.kill, self.read, self.list_pids, self.clock = kill, read, list_pids, clock
        self.spec = load_base()
        self.base_exact: frozenset[str] = frozenset()  # aus der Paketkette (refresh_base)
        self._pacman_mtime: float | None = None
        self.names: dict[str, str] = {}             # App-Schlüssel -> Anzeigename (aus .desktop)
        self.handled: set[tuple[int, int]] = set()  # bereits geprüfte Prozesse (pid, start)
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
                        uid = pwd.getpwnam(user).pw_uid
                    except KeyError:
                        continue
                    out[uid] = (user, child.program_mode,
                                Allowed(self.spec.globs, set(child.allowed_programs), self.base_exact,
                                        self.spec.excludes))
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

    def on_exec(self, pid: int) -> None:
        """Netlink-Callback (synchron) – Prüfung als Task."""
        task = asyncio.get_running_loop().create_task(self.check_pid(pid))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def check_pid(self, pid: int) -> tuple[int, int] | None:
        targets = self.targets()
        if not targets:
            return None
        info = self.read(pid)
        if info is None or info.uid not in targets:
            return None
        if info.ident in self.handled:
            return info.ident
        self.handled.add(info.ident)
        user, mode, allowed = targets[info.uid]
        if mode == "enforce" and not self.base_exact:
            mode = "audit"  # Sicherung: ohne Basisliste nie beenden (würde die Sitzung zerstören)
        verdict = classify(info, allowed)
        if verdict is None:
            return info.ident
        name = self.names.get(verdict.key, verdict.name)
        if mode == "enforce":
            try:
                self.kill(pid, signal.SIGKILL)
                log.warning("Gesperrt: %s (%s) von %s, PID %s", name, verdict.key, user, pid)
            except ProcessLookupError:
                pass
            await self._notify(user, info.uid, verdict.key, name)
        else:
            log.info("Protokolliert (würde sperren): %s (%s) von %s", name, verdict.key, user)
        self._record(user, verdict, name, audit=mode != "enforce")
        return info.ident

    async def _notify(self, user: str, uid: int, key: str, name: str) -> None:
        now = self.clock()
        if now - self.last_notify.get((user, key), -1e9) < NOTIFY_DEDUP:
            return
        self.last_notify[(user, key)] = now
        await self.agent.desktop.notify(user, uid, "Programm gesperrt",
                                        f"„{name}“ ist nicht freigegeben. Frag deine Eltern.")

    def _record(self, user: str, verdict, name: str, audit: bool) -> None:
        now = self.clock()
        k = (user, verdict.key)
        if now - self.last_report.get(k, -1e9) < REPORT_DEDUP["audit" if audit else "enforce"]:
            return
        self.last_report[k] = now
        self.pending.append(p.BlockedEvent(username=user, at=datetime.now(timezone.utc), key=verdict.key[:512],
                                           name=name[:255], detail=verdict.detail, audit=audit))
        del self.pending[:-MAX_PENDING]
        self._save()

    def _save(self) -> None:
        store.save(BLOCKED_FILE, {"events": [e.model_dump(mode="json") for e in self.pending]})

    # ---------- Basisliste aus der Paketkette ----------

    def base_outdated(self) -> bool:
        try:
            return os.stat(PACMAN_DB).st_mtime != self._pacman_mtime
        except OSError:
            return False

    async def refresh_base(self, app_exes: set[str]) -> None:
        """Basisliste neu berechnen (pacman, im Thread). Bei Fehlern bleibt die alte erhalten."""
        try:
            mtime = os.stat(PACMAN_DB).st_mtime
            exact = await asyncio.to_thread(_compute_base, self.spec.packages, app_exes, self.spec.excludes)
        except (OSError, subprocess.SubprocessError) as e:
            log.error("Basisliste nicht berechenbar (%s) – Programmkontrolle protokolliert nur", e)
            return
        self.base_exact, self._pacman_mtime = exact, mtime
        self.policy_changed()
        log.info("Basisliste: %d Programme aus %d Wurzelpaketen", len(exact), len(self.spec.packages))

    # ---------- Server ----------

    async def flush(self, conn) -> None:
        if not self.pending or conn is None or not conn.connected:
            return
        batch = list(self.pending)
        if await conn.send(p.BlockedReport(events=batch)):
            self.pending = self.pending[len(batch):]
            self._save()


def _compute_base(packages: list[str], app_exes: set[str], excludes: list[str]) -> frozenset[str]:
    env = {"LC_ALL": "C", "PATH": "/usr/bin"}
    qi = subprocess.run(["pacman", "-Qi"], capture_output=True, text=True, env=env, check=True, timeout=60)
    deps, provides = parse_pacman_qi(qi.stdout)
    pkgs = sorted(closure(deps, provides, packages))
    if not pkgs:
        return frozenset()
    ql = subprocess.run(["pacman", "-Qlq", *pkgs], capture_output=True, text=True, env=env, check=True,
                        timeout=60)
    files = [f for f in ql.stdout.split() if os.path.isfile(f) and os.access(f, os.X_OK)]
    return base_paths(files, app_exes, excludes)
