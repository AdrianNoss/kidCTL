"""Prozessinformationen aus /proc und Netlink-Prozess-Events (PROC_EVENTS, root nötig)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import struct
from dataclasses import dataclass

log = logging.getLogger("kidctl.procs")

FLATPAK_SCOPE = re.compile(r"app-flatpak-(?P<id>[A-Za-z0-9_.\-]+?)-\d+\.scope")


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    uid: int
    ppid: int
    start: int           # Startzeit (Ticks seit Boot) – schützt vor PID-Wiederverwendung
    exe: str             # realpath aus /proc/<pid>/exe (Sicht des Prozesses)
    argv: tuple[str, ...]
    tty: bool            # hat ein Steuerterminal (interaktiv)
    flatpak_id: str | None
    parent_exe: str = ""

    @property
    def ident(self) -> tuple:
        """Kennung für „schon geprüft“. Enthält Programm und Argumente: exec() ändert beides,
        PID und Startzeit aber nicht (z. B. systemd-executor -> App beim Start aus Plasma)."""
        return (self.pid, self.start, self.exe, self.argv)


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _exe(pid: int) -> str | None:
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None  # Kernel-Thread, Zombie oder schon beendet
    return exe.removesuffix(" (deleted)")


def read_proc(pid: int) -> ProcInfo | None:
    stat = _read(f"/proc/{pid}/stat")
    status = _read(f"/proc/{pid}/status")
    exe = _exe(pid)
    if stat is None or status is None or exe is None:
        return None
    # comm kann Leerzeichen/Klammern enthalten -> nach der letzten ")" aufteilen
    fields = stat[stat.rfind(b")") + 2:].split()
    ppid, tty_nr, start = int(fields[1]), int(fields[4]), int(fields[19])
    uid = next((int(line.split()[1]) for line in status.splitlines() if line.startswith(b"Uid:")), -1)
    argv = tuple(a.decode(errors="replace") for a in (_read(f"/proc/{pid}/cmdline") or b"").split(b"\0") if a)
    cgroup = (_read(f"/proc/{pid}/cgroup") or b"").decode(errors="replace")
    m = FLATPAK_SCOPE.search(cgroup)
    return ProcInfo(pid=pid, uid=uid, ppid=ppid, start=start, exe=exe, argv=argv, tty=tty_nr != 0,
                    flatpak_id=m.group("id") if m else None, parent_exe=_exe(ppid) or "")


def pids_of(uids: set[int]) -> list[int]:
    """Alle PIDs, deren reale UID in ``uids`` liegt (schneller Vorfilter über /proc/<pid>)."""
    out = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                if os.stat(f"/proc/{name}").st_uid in uids:
                    out.append(int(name))
            except OSError:
                pass
    return out


# ---------- Netlink PROC_EVENTS ----------

NETLINK_CONNECTOR = 11
CN_IDX_PROC = 1
CN_VAL_PROC = 1
PROC_CN_MCAST_LISTEN = 1
PROC_EVENT_EXEC = 0x00000002
NLMSG_DONE = 3

_NLMSGHDR = struct.Struct("=IHHII")    # len, type, flags, seq, pid
_CN_MSG = struct.Struct("=IIIIHH")     # idx, val, seq, ack, len, flags
_PROC_EVENT = struct.Struct("=IIQ")    # what, cpu, timestamp_ns


class ProcEvents:
    """Ruft ``on_exec(pid)`` bei jedem exec() auf. Ohne Berechtigung: inaktiv (Scan genügt)."""

    def __init__(self, on_exec) -> None:
        self.on_exec = on_exec
        self.sock: socket.socket | None = None

    def start(self) -> bool:
        try:
            s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK, NETLINK_CONNECTOR)
            s.bind((0, CN_IDX_PROC))
            op = struct.pack("=I", PROC_CN_MCAST_LISTEN)
            cn = _CN_MSG.pack(CN_IDX_PROC, CN_VAL_PROC, 0, 0, len(op), 0) + op
            s.send(_NLMSGHDR.pack(_NLMSGHDR.size + len(cn), NLMSG_DONE, 0, 0, os.getpid()) + cn)
        except OSError as e:
            log.warning("Netlink PROC_EVENTS nicht verfügbar (%s) – nur periodischer Scan", e)
            return False
        self.sock = s
        asyncio.get_running_loop().add_reader(s.fileno(), self._readable)
        log.info("Netlink PROC_EVENTS aktiv")
        return True

    def stop(self) -> None:
        if self.sock is not None:
            asyncio.get_running_loop().remove_reader(self.sock.fileno())
            self.sock.close()
            self.sock = None

    def _readable(self) -> None:
        while True:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                return
            except OSError as e:  # z. B. ENOBUFS bei Überlast – Scan fängt Verpasstes
                log.debug("Netlink: %s", e)
                return
            for pid in parse_exec_events(data):
                self.on_exec(pid)


def parse_exec_events(data: bytes) -> list[int]:
    pids = []
    off = 0
    while off + _NLMSGHDR.size <= len(data):
        length = _NLMSGHDR.unpack_from(data, off)[0]
        if length < _NLMSGHDR.size:
            break
        ev = off + _NLMSGHDR.size + _CN_MSG.size
        if ev + _PROC_EVENT.size <= off + length:
            what = _PROC_EVENT.unpack_from(data, ev)[0]
            if what == PROC_EVENT_EXEC:
                _pid, tgid = struct.unpack_from("=ii", data, ev + _PROC_EVENT.size)
                pids.append(tgid)  # Prozess-ID (process_pid wäre die Thread-ID)
        off += (length + 3) & ~3
    return pids
