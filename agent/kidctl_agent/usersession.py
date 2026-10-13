"""Aktionen in der Desktop-Sitzung eines Users: KDE-Benachrichtigung, saubere Abmeldung,
Wiederherstellung des Anmeldebildschirms."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd

log = logging.getLogger("kidctl.usersession")

TIMEOUT = 10  # Sekunden
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

    async def _spawn(self, cmd: list[str]) -> asyncio.subprocess.Process | None:
        try:
            return await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            log.warning("%s nicht ausführbar", cmd[0], exc_info=True)
            return None

    async def notify(self, user: str, uid: int, title: str, body: str, critical: bool = False) -> None:
        cmd = self._as_user(user, uid, "notify-send", "-a", "kidctl", "-i", "chronometer",
                            "-u", "critical" if critical else "normal", title, body)
        if cmd and (proc := await self._spawn(cmd)):
            task = asyncio.create_task(_reap(proc, f"notify-send für {user}"))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)

    async def logout(self, user: str, uid: int) -> bool:
        """Bittet Plasma um Abmeldung ohne Rückfrage. So beendet sich sddm-helper regulär und
        SDDM zeigt wieder den Anmeldebildschirm (bei TerminateSession nicht der Fall)."""
        cmd = self._as_user(user, uid, "busctl", "--user", "call",
                            "org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown", "logout")
        if not cmd or not (proc := await self._spawn(cmd)):
            return False
        return await _reap(proc, f"Plasma-Abmeldung für {user}") == 0

    async def restart_display_manager(self) -> None:
        log.warning("Kein Anmeldebildschirm mehr – starte display-manager.service neu")
        if proc := await self._spawn(["systemctl", "restart", "display-manager.service"]):
            await _reap(proc, "Neustart display-manager")


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
