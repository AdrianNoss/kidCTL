"""Unix-Socket für die Tray-App. Identität ausschließlich über SO_PEERCRED."""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
import socket
import struct
from pathlib import Path

from pydantic import BaseModel, ValidationError

from kidctl_common import tray as t

from kidctl_common import i18n
from kidctl_common.i18n import _

from . import loginlock

log = logging.getLogger("kidctl.tray")

MAX_LINE = 64 * 1024
STATE_REFRESH = 30.0  # Sekunden: Zustand spätestens so oft senden (Restzeit-Anzeige)


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


class _Client:
    def __init__(self, user: str, writer: asyncio.StreamWriter) -> None:
        self.user = user
        self.writer = writer
        self.last_state: t.TrayState | None = None
        self.last_sent = 0.0
        self.tray_icon: bool | None = None


class TrayServer:
    """``handler`` liefert ``tray_state(user)``, ``tray_history(user)`` und nimmt
    ``tray_chat_send(user, body)`` / ``tray_request(user, msg)`` entgegen."""

    def __init__(self, handler, path: str | None = None) -> None:
        self.handler = handler
        self.path = Path(path or (loginlock.RUN_DIR / "tray.sock"))
        self.clients: set[_Client] = set()
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path), limit=MAX_LINE)
        os.chmod(self.path, 0o666)  # jeder User darf verbinden – Berechtigung prüft SO_PEERCRED
        log.info("Tray-Socket: %s", self.path)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        for c in list(self.clients):
            c.writer.close()

    # ---------- Senden ----------

    async def _send(self, client: _Client, msg: BaseModel) -> None:
        try:
            client.writer.write(msg.model_dump_json().encode() + b"\n")
            await client.writer.drain()
        except (ConnectionError, RuntimeError):
            self.clients.discard(client)

    async def send_to_user(self, user: str, msg: BaseModel) -> int:
        targets = [c for c in self.clients if c.user == user]
        for c in targets:
            await self._send(c, msg)
        return len(targets)

    def tray_icons(self) -> dict[str, bool]:
        """User -> Tray-Symbol sichtbar (in mindestens einer verbundenen Tray-App); ohne Meldung kein Eintrag."""
        out: dict[str, bool] = {}
        for c in list(self.clients):
            if c.tray_icon is not None:
                out[c.user] = out.get(c.user, False) or c.tray_icon
        return out

    async def push_states(self, now: float) -> None:
        """Zustand an alle Clients – bei Änderung sofort, sonst alle ``STATE_REFRESH`` s."""
        for c in list(self.clients):
            state = self.handler.tray_state(c.user)
            if state != c.last_state or now - c.last_sent >= STATE_REFRESH:
                c.last_state, c.last_sent = state, now
                await self._send(c, state)

    # ---------- Empfang ----------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            uid = peer_uid(writer.get_extra_info("socket"))
            user = pwd.getpwuid(uid).pw_name
        except (OSError, KeyError):
            writer.close()
            return
        client = _Client(user, writer)
        self.clients.add(client)
        log.debug("Tray verbunden: %s (UID %s)", user, uid)
        try:
            while line := await reader.readline():
                try:
                    msg = t.tray_to_agent.validate_json(line)
                except ValidationError as e:
                    with i18n.use(self.handler.lang_of(client.user)):
                        text = _("ungültige Nachricht: {n} Fehler").format(n=e.error_count())
                    await self._send(client, t.TrayError(message=text))
                    continue
                await self._dispatch(client, msg)
        except (ConnectionError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            self.clients.discard(client)
            writer.close()

    async def _dispatch(self, client: _Client, msg) -> None:
        user = client.user
        if isinstance(msg, t.TrayHello):
            client.tray_icon = msg.tray_icon
            state = self.handler.tray_state(user)
            client.last_state = state
            await self._send(client, state)
            if state.restricted:
                await self._send(client, t.TrayChat(messages=self.handler.tray_history(user)))
            return
        if not self.handler.tray_state(user).restricted:
            with i18n.use(self.handler.lang_of(user)):
                await self._send(client, t.TrayError(message=_("Dieser Benutzer ist keinem Kind zugeordnet.")))
            return
        if isinstance(msg, t.TrayChatSend):
            online = await self.handler.tray_chat_send(user, msg.body)
            await self._send(client, t.TrayQueued(kind="chat_send", online=online))
        elif isinstance(msg, t.TrayExtensionRequest):
            if not msg.minutes and (msg.window_end is None or msg.target == "games"):
                with i18n.use(self.handler.lang_of(user)):
                    await self._send(client, t.TrayError(message=_("Bitte Minuten und/oder ein späteres Ende angeben.")))
                return
            online = await self.handler.tray_request(user, msg)
            await self._send(client, t.TrayQueued(kind="extension_request", online=online))
        elif isinstance(msg, t.TrayGetBlocked):
            await self._send(client, self.handler.tray_blocked(user))
        elif isinstance(msg, t.TrayAccessRequest):
            online, error = await self.handler.tray_access(user, msg.key, msg.reason)
            if error:
                await self._send(client, t.TrayError(message=error))
            else:
                await self._send(client, t.TrayQueued(kind="access_request", online=online))
                await self._send(client, self.handler.tray_blocked(user))
