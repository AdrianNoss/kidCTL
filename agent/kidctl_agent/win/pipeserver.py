"""Named Pipe ``\\\\.\\pipe\\kidctl-tray`` für die Tray-App (Ersatz für den Unix-Socket).

Identität ausschließlich über das Betriebssystem: Prozess-ID des Clients (``GetNamedPipeClientProcessId``) ->
Token -> SID -> lokales Konto. Die Pipe lässt nur lokale, interaktive Benutzer zu (``IU``, keine Netzwerk-
clients); das Protokoll (JSON-Zeilen) und die Verarbeitung sind dieselben wie unter Linux (``TrayServer``).

Je Verbindung lesen und schreiben zwei Threads mit Overlapped-I/O; die Daten gehen über die Ereignisschleife
an einen ``asyncio.StreamReader``. Die erste Pipe-Instanz wird mit ``FILE_FLAG_FIRST_PIPE_INSTANCE`` angelegt:
Hätte ein Benutzer den Namen vor dem Dienst belegt, scheitert der Start (Log) statt still mitzulesen.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import queue
import threading

from kidctl_common import tray as t

from ..trayserver import MAX_LINE, TrayServer
from . import api
from .accounts import Accounts

log = logging.getLogger("kidctl.tray")

PIPE_PATH = rf"\\.\pipe\{t.PIPE_NAME}"
# SYSTEM und Administratoren voll, interaktive Benutzer lesen/schreiben (Qt öffnet mit GENERIC_READ|WRITE)
PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"
READ_SIZE = 4096


class _PipeWriter:
    """Schreibseite im Stil von ``asyncio.StreamWriter`` (``write``/``drain``/``close``)."""

    def __init__(self, conn: _PipeConn) -> None:
        self.conn = conn

    def write(self, data: bytes) -> None:
        if self.conn.closed:
            raise ConnectionError("Pipe geschlossen")
        self.conn.out.put(data)

    async def drain(self) -> None:
        if self.conn.closed:
            raise ConnectionError("Pipe geschlossen")

    def close(self) -> None:
        self.conn.close()

    def get_extra_info(self, name: str, default=None):
        return default


class _PipeConn:
    def __init__(self, handle: int, loop: asyncio.AbstractEventLoop, stop_event: int) -> None:
        self.handle = handle
        self.loop = loop
        self.stop_event = stop_event
        self.reader = asyncio.StreamReader(limit=MAX_LINE, loop=loop)
        self.out: queue.Queue[bytes | None] = queue.Queue()
        self.closed = False
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._read_loop, daemon=True, name="kidctl-pipe-read"),
                         threading.Thread(target=self._write_loop, daemon=True, name="kidctl-pipe-write")]
        self._alive = len(self._threads)

    def start(self) -> None:
        for th in self._threads:
            th.start()

    def _read_loop(self) -> None:
        ev = api.create_event()
        buf = ctypes.create_string_buffer(READ_SIZE)
        try:
            while not self.closed:
                ov = api.OVERLAPPED(hEvent=ev)
                api.reset_event(ev)
                api.start_read(self.handle, buf, ov)
                if api.wait_any([ev, self.stop_event]) != 0:
                    api.cancel_io(self.handle)
                    break
                n = api.overlapped_result(self.handle, ov)
                if n:
                    self.loop.call_soon_threadsafe(self.reader.feed_data, buf.raw[:n])
        except OSError as e:
            if e.errno not in (api.ERROR_BROKEN_PIPE, api.ERROR_OPERATION_ABORTED, api.ERROR_NO_DATA):
                log.debug("Pipe lesen: %s", e)
        finally:
            api.close_handle(ev)
            self.loop.call_soon_threadsafe(self.reader.feed_eof)
            self.close()
            self._thread_done()

    def _write_loop(self) -> None:
        ev = api.create_event()
        try:
            while True:
                data = self.out.get()
                if data is None or self.closed:
                    break
                ov = api.OVERLAPPED(hEvent=ev)
                api.reset_event(ev)
                api.start_write(self.handle, data, ov)
                api.overlapped_result(self.handle, ov)
        except OSError as e:
            log.debug("Pipe schreiben: %s", e)
        finally:
            api.close_handle(ev)
            self.close()
            self._thread_done()

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
        self.out.put(None)
        api.cancel_io(self.handle)

    def _thread_done(self) -> None:
        with self._lock:
            self._alive -= 1
            last = self._alive == 0
        if last:   # Handle erst schließen, wenn beide Threads fertig sind
            api.disconnect_pipe(self.handle)
            api.close_handle(self.handle)


class PipeTrayServer(TrayServer):
    """Wie ``TrayServer``, aber über eine Named Pipe; Identität über das Token des Client-Prozesses."""

    def __init__(self, handler, accounts: Accounts, path: str = PIPE_PATH) -> None:
        super().__init__(handler, path="/nonexistent")
        self.pipe_path = path
        self.accounts = accounts
        self._stop_event: int | None = None
        self._accept: threading.Thread | None = None
        self._conns: set[_PipeConn] = set()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._sa, self._psd = api.security_attributes(PIPE_SDDL)
        self._stop_event = api.create_event()
        first = api.create_pipe(self.pipe_path, self._sa, first=True)   # wirft, wenn der Name belegt ist
        self._accept = threading.Thread(target=self._accept_loop, args=(loop, first), daemon=True,
                                        name="kidctl-pipe-accept")
        self._accept.start()
        log.info("Tray-Pipe: %s", self.pipe_path)

    async def stop(self) -> None:
        if self._stop_event is not None:
            api.set_event(self._stop_event)
        await super().stop()
        for c in list(self._conns):
            c.close()

    def _accept_loop(self, loop: asyncio.AbstractEventLoop, handle: int) -> None:
        ev = api.create_event()
        try:
            while True:
                ov = api.OVERLAPPED(hEvent=ev)
                api.reset_event(ev)
                try:
                    if not api.connect_pipe(handle, ov):
                        if api.wait_any([ev, self._stop_event]) != 0:
                            api.cancel_io(handle)
                            api.close_handle(handle)
                            return
                        api.overlapped_result(handle, ov)
                except OSError:
                    log.exception("Tray-Pipe: Verbindungsannahme fehlgeschlagen")
                    api.close_handle(handle)
                    handle = api.create_pipe(self.pipe_path, self._sa, first=False)
                    continue
                conn = _PipeConn(handle, loop, self._stop_event)
                asyncio.run_coroutine_threadsafe(self._welcome(conn), loop)
                handle = api.create_pipe(self.pipe_path, self._sa, first=False)
        except Exception:
            log.exception("Tray-Pipe beendet")
        finally:
            api.close_handle(ev)

    def _identify(self, conn: _PipeConn) -> str | None:
        try:
            sid = api.process_user_sid(api.pipe_client_pid(conn.handle))
        except OSError:
            log.exception("Tray-Pipe: Client nicht identifizierbar")
            return None
        account = self.accounts.by_sid(sid)
        return account.wire if account else None

    async def _welcome(self, conn: _PipeConn) -> None:
        user = await asyncio.to_thread(self._identify, conn)
        conn.start()
        if user is None:
            conn.close()
            return
        self._conns.add(conn)
        log.debug("Tray verbunden: %s", user)
        try:
            await self.serve(user, conn.reader, _PipeWriter(conn))
        finally:
            self._conns.discard(conn)
