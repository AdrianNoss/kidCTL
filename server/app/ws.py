"""Verwaltung der aktiven Agent-WebSocket-Verbindungen."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

log = logging.getLogger("kidctl.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: int, ws: WebSocket) -> WebSocket | None:
        """Registriert ``ws``; gibt eine evtl. ersetzte ältere Verbindung zurück."""
        async with self._lock:
            old = self._conns.get(device_id)
            self._conns[device_id] = ws
            return old

    async def unregister(self, device_id: int, ws: WebSocket) -> None:
        async with self._lock:
            if self._conns.get(device_id) is ws:
                del self._conns[device_id]

    def is_online(self, device_id: int) -> bool:
        return device_id in self._conns

    async def disconnect(self, device_id: int, code: int, reason: str = "") -> None:
        ws = self._conns.get(device_id)
        if ws is None:
            return
        try:
            await ws.close(code=code, reason=reason)
        except Exception:  # Verbindung evtl. schon weg
            log.debug("close für Gerät %s fehlgeschlagen", device_id, exc_info=True)

    async def send(self, device_id: int, message: dict) -> bool:
        ws = self._conns.get(device_id)
        if ws is None:
            return False
        await ws.send_json(message)
        return True


manager = ConnectionManager()
