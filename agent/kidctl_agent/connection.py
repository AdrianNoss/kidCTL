"""Dauerhafte WebSocket-Verbindung zum Server mit Reconnect und Backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import sys

from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from kidctl_common import protocol as p

from . import __version__, store, tls

log = logging.getLogger("kidctl.conn")

BACKOFF_MIN, BACKOFF_MAX = 2.0, 300.0
RETRY_UNAUTHORIZED = 3600.0      # Token ungültig/widerrufen: selten erneut versuchen
RETRY_UNSUPPORTED = 6 * 3600.0   # Agent zu alt: bis zum Update über pacman


def ws_url(server_url: str) -> str:
    base = server_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + p.WS_PATH


class Connection:
    """Ruft ``handler.on_connected(conn)`` und ``handler.on_message(msg)`` auf."""

    def __init__(self, device: dict, handler) -> None:
        self.url = ws_url(device["server_url"])
        if not self.url.startswith("wss://") and not device.get("insecure_http"):
            raise ValueError(f"unverschlüsselte Serveradresse {device['server_url']} – neu koppeln mit https://")
        self.token = device["token"]
        self.device = device
        self.handler = handler
        self._ws = None
        self._welcomed = False

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def send(self, msg: BaseModel) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(msg.model_dump_json())
            return True
        except ConnectionClosed:
            return False

    async def run_forever(self) -> None:
        backoff = BACKOFF_MIN
        while True:
            self._welcomed = False
            delay = None
            try:
                await self._session()
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                if code in (p.CLOSE_UNAUTHORIZED, p.CLOSE_REVOKED):
                    log.error("Server lehnt Geräte-Token ab (Code %s) – neu koppeln: kidctl enroll", code)
                    delay = RETRY_UNAUTHORIZED
                elif code == p.CLOSE_UNSUPPORTED_VERSION:
                    log.error("Agent-Protokoll vom Server nicht unterstützt – Paket aktualisieren")
                    delay = RETRY_UNSUPPORTED
                else:
                    log.info("Verbindung getrennt (Code %s)", code)
            except (OSError, InvalidStatus, WebSocketException, asyncio.TimeoutError) as e:
                log.info("Server nicht erreichbar: %s", e)
            finally:
                self._ws = None
            if self._welcomed:
                backoff = BACKOFF_MIN  # war verbunden -> zügig neu verbinden
            if delay is None:
                delay = backoff
                backoff = min(backoff * 2, BACKOFF_MAX)
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))

    async def _session(self) -> None:
        ssl_context = tls.context() if self.url.startswith("wss://") else None
        async with connect(self.url, ssl=ssl_context, additional_headers={"Authorization": f"Bearer {self.token}"},
                           open_timeout=15, ping_interval=30, ping_timeout=30,
                           max_size=4 * 1024 * 1024) as ws:
            await ws.send(self._hello())
            first = p.server_message_adapter.validate_json(await asyncio.wait_for(ws.recv(), 15))
            if not isinstance(first, p.Welcome):
                log.error("Handshake abgelehnt: %s", first)
                await ws.recv()  # Server schließt -> ConnectionClosed mit Close-Code
                return
            log.info("Verbunden mit %s (Gerät %s)", self.url, first.device_id)
            self._welcomed = True
            self._ws = ws
            await self.handler.on_connected(self)
            async for raw in ws:
                try:
                    msg = p.server_message_adapter.validate_json(raw)
                except ValidationError:
                    log.debug("Unbekannte/ungültige Nachricht ignoriert: %.200s", raw)
                    continue
                if isinstance(msg, p.Relocate):
                    await self._relocate(msg.server_url)
                    continue
                await self.handler.on_message(msg)

    @staticmethod
    def _hello() -> str:
        return p.Hello(protocol_version=p.PROTOCOL_VERSION, agent_version=__version__, hostname=socket.gethostname(),
                       platform="windows" if sys.platform == "win32" else "linux").model_dump_json()

    async def _probe(self, url: str) -> bool:
        """Meldet sich das Gerät mit seinem Token beim neuen Server erfolgreich an (``welcome``)?"""
        try:
            async with connect(url, ssl=tls.context(), additional_headers={"Authorization": f"Bearer {self.token}"},
                               open_timeout=15, max_size=4 * 1024 * 1024) as ws:
                await ws.send(self._hello())
                first = p.server_message_adapter.validate_json(await asyncio.wait_for(ws.recv(), 15))
                return isinstance(first, p.Welcome)
        except (OSError, InvalidStatus, WebSocketException, asyncio.TimeoutError, ValidationError) as e:
            log.info("Umzugsziel %s nicht bestätigt: %s", url, e)
            return False

    async def _relocate(self, server_url: str) -> None:
        """Server-Umzug: neue Adresse nur übernehmen, wenn der neue Server das Gerät anerkennt."""
        url = ws_url(server_url)
        if url == self.url:
            return
        if not await self._probe(url):
            log.warning("Umzug nach %s abgelehnt: neuer Server erkennt das Gerät nicht – bleibe bei %s",
                        server_url, self.device.get("server_url"))
            return
        old = self.device.get("server_url")
        self.device.update(server_url=server_url, previous_server_url=old)
        store.save(store.DEVICE_FILE, self.device)
        log.warning("Server umgezogen: %s -> %s", old, server_url)
        self.url = url
        if self._ws is not None:
            await self._ws.close()   # neu verbinden, jetzt mit dem neuen Server
