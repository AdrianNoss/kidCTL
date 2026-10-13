"""KDE-Benachrichtigungen in der Sitzung eines Users (notify-send über dessen Session-Bus)."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd

log = logging.getLogger("kidctl.notify")

TIMEOUT = 10  # Sekunden
_tasks: set[asyncio.Task] = set()


class Notifier:
    async def notify(self, user: str, uid: int, title: str, body: str, critical: bool = False) -> None:
        runtime = f"/run/user/{uid}"
        if not os.path.exists(f"{runtime}/bus"):
            log.debug("kein Session-Bus für %s – Benachrichtigung entfällt", user)
            return
        try:
            gid = pwd.getpwnam(user).pw_gid
        except KeyError:
            return
        # setpriv statt runuser/su: wechselt nur die IDs, öffnet keine PAM-Sitzung
        cmd = ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--init-groups", "--reset-env", "--",
               "env", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus", f"XDG_RUNTIME_DIR={runtime}",
               "notify-send", "-a", "kidctl", "-i", "chronometer",
               "-u", "critical" if critical else "normal", title, body]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            log.warning("notify-send nicht ausführbar", exc_info=True)
            return
        task = asyncio.create_task(_reap(proc, user))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _reap(proc: asyncio.subprocess.Process, user: str) -> None:
    """Wartet nicht blockierend auf notify-send; hängt es (kein Benachrichtigungsdienst), wird
    es nach ``TIMEOUT`` beendet."""
    try:
        await asyncio.wait_for(proc.wait(), TIMEOUT)
    except asyncio.TimeoutError:
        log.info("notify-send für %s hängt (kein Benachrichtigungsdienst?) – abgebrochen", user)
        proc.kill()
        await proc.wait()
