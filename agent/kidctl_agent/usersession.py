"""Aktionen in der Desktop-Sitzung eines Users: Benachrichtigung, saubere Abmeldung (Plasma bzw. GNOME),
Wiederherstellung des Anmeldebildschirms."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd

from .procs import pids_of, read_proc

log = logging.getLogger("kidctl.usersession")

TIMEOUT = 10  # Sekunden
ACTION_TIMEOUT = 900  # so lange auf einen Klick in einer Benachrichtigung mit Knopf warten
ACTION_REQUEST = "request"
SHELLS = {"gnome-shell": "gnome", "plasmashell": "plasma"}   # Prozess der Desktop-Shell -> Desktop
# Abmeldung ohne Rückfrage je Desktop (busctl --user call …)
LOGOUT_CALLS = {
    "plasma": ("org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown", "logout"),
    # Modus 1: ohne Bestätigungsdialog
    "gnome": ("org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager", "Logout", "u", "1"),
}
_tasks: set[asyncio.Task] = set()


class UserSession:
    def _as_user(self, user: str, uid: int, *cmd: str) -> list[str] | None:
        """Befehl im Kontext des Users mit dessen Session-Bus.

        setpriv statt runuser/su: wechselt nur die IDs, öffnet keine PAM-Sitzung.
        """
        runtime = f"/run/user/{uid}"
        if not os.path.exists(f"{runtime}/bus"):
            log.debug("kein Session-Bus für %s", user)
            return None
        try:
            gid = pwd.getpwnam(user).pw_gid
        except KeyError:
            return None
        return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--init-groups", "--reset-env", "--",
                "env", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus", f"XDG_RUNTIME_DIR={runtime}",
                *cmd]

    async def _spawn(self, cmd: list[str], capture: bool = False) -> asyncio.subprocess.Process | None:
        try:
            return await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            log.warning("%s nicht ausführbar", cmd[0], exc_info=True)
            return None

    async def notify(self, user: str, uid: int, title: str, body: str, critical: bool = False,
                     on_action=None, action: str = "Verlängerung anfordern") -> None:
        """Desktop-Benachrichtigung. Mit ``on_action`` zusätzlich ein Knopf (Beschriftung ``action``);
        ein Klick ruft ``on_action(user)`` auf."""
        args = ["notify-send", "-a", "kidctl", "-i", "kidctl",
                "-u", "critical" if critical else "normal"]
        if on_action:
            args += [f"--action={ACTION_REQUEST}={action}", "--wait"]
        cmd = self._as_user(user, uid, *args, "--", title, body)  # Texte nie als Optionen
        if not cmd or not (proc := await self._spawn(cmd, capture=bool(on_action))):
            return
        if on_action:
            coro = _await_action(proc, user, on_action)
        else:
            coro = _reap(proc, f"notify-send für {user}")
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    async def logout(self, user: str, uid: int, desktop: str = "") -> bool:
        """Bittet den Desktop (Plasma bzw. GNOME) um Abmeldung ohne Rückfrage. So beendet sich der
        Sitzungsleiter (sddm-helper, gdm-session-worker) regulär und der Display-Manager zeigt wieder den
        Anmeldebildschirm (bei TerminateSession unter SDDM nicht der Fall).
        Ist der Desktop unbekannt (GDM trägt ihn nicht in logind ein), entscheiden die laufenden Prozesse des
        Users (gnome-shell bzw. plasmashell); sonst nacheinander GNOME und Plasma. Nicht umgekehrt: Ist Plasma
        installiert, startet D-Bus ``org.kde.Shutdown`` auch in einer GNOME-Sitzung – der Aufruf „gelingt“."""
        if desktop:
            kinds: tuple[str, ...] = (desktop_kind(desktop),)
        else:
            kinds = (running,) if (running := self.running_desktop(uid)) else ("gnome", "plasma")
        for kind in kinds:
            cmd = self._as_user(user, uid, "busctl", "--user", "call", *LOGOUT_CALLS[kind])
            if not cmd or not (proc := await self._spawn(cmd)):
                return False
            if await _reap(proc, f"Abmeldung ({kind}) für {user}") == 0:
                return True
        return False

    def running_desktop(self, uid: int) -> str | None:
        """``gnome`` bzw. ``plasma`` nach der laufenden Shell des Users, sonst ``None``."""
        for pid in pids_of({uid}):
            if (info := read_proc(pid)) is not None and (kind := SHELLS.get(os.path.basename(info.exe))):
                return kind
        return None

    async def restart_display_manager(self) -> None:
        log.warning("Kein Anmeldebildschirm mehr – starte display-manager.service neu")
        if proc := await self._spawn(["systemctl", "restart", "display-manager.service"]):
            await _reap(proc, "Neustart display-manager")


def desktop_kind(desktop: str) -> str:
    """``gnome`` für GNOME-Sitzungen (XDG_SESSION_DESKTOP ``gnome``, ``GNOME``, ``ubuntu:GNOME`` …), sonst
    ``plasma``."""
    return "gnome" if "gnome" in desktop.lower() else "plasma"


async def _reap(proc: asyncio.subprocess.Process, what: str) -> int | None:
    """Wartet auf den Prozess; hängt er (z. B. kein Benachrichtigungsdienst), wird er nach
    ``TIMEOUT`` beendet."""
    try:
        return await asyncio.wait_for(proc.wait(), TIMEOUT)
    except asyncio.TimeoutError:
        log.info("%s hängt – abgebrochen", what)
        proc.kill()
        await proc.wait()
        return None


async def _await_action(proc: asyncio.subprocess.Process, user: str, on_action) -> None:
    """notify-send --wait gibt den Schlüssel der geklickten Aktion aus."""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), ACTION_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return
    if out.decode(errors="replace").strip() == ACTION_REQUEST:
        try:
            await on_action(user)
        except Exception:
            log.exception("Aktion für %s fehlgeschlagen", user)
