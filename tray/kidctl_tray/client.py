"""Verbindung zum Agenten über den lokalen Unix-Socket (QLocalSocket), mit Reconnect."""

from __future__ import annotations

import os

from pydantic import BaseModel, ValidationError
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from kidctl_common import tray as t

RECONNECT_MS = 3000


class AgentClient(QObject):
    connectionChanged = Signal(bool)
    stateChanged = Signal(object)        # t.TrayState
    historyReceived = Signal(list)       # list[ChatMessage]
    messageReceived = Signal(object)     # ChatMessage (neu oder geändert)
    queued = Signal(object)              # t.TrayQueued
    openRequestDialog = Signal()
    errorReceived = Signal(str)

    def __init__(self, path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.path = path or os.environ.get("KIDCTL_TRAY_SOCKET", t.SOCKET_PATH)
        self.sock = QLocalSocket(self)
        self.sock.connected.connect(self._on_connected)
        self.sock.disconnected.connect(self._on_disconnected)
        self.sock.errorOccurred.connect(lambda _e: self._schedule_reconnect())
        self.sock.readyRead.connect(self._on_ready_read)
        self._timer = QTimer(self, singleShot=True, interval=RECONNECT_MS)
        self._timer.timeout.connect(self.connect_to_agent)

    @property
    def is_connected(self) -> bool:
        return self.sock.state() == QLocalSocket.LocalSocketState.ConnectedState

    def connect_to_agent(self) -> None:
        if self.sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.sock.connectToServer(self.path)

    def _schedule_reconnect(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _on_connected(self) -> None:
        self.connectionChanged.emit(True)
        self.send(t.TrayHello())

    def _on_disconnected(self) -> None:
        self.connectionChanged.emit(False)
        self._schedule_reconnect()

    def send(self, msg: BaseModel) -> bool:
        if not self.is_connected:
            return False
        self.sock.write(msg.model_dump_json().encode() + b"\n")
        self.sock.flush()
        return True

    def _on_ready_read(self) -> None:
        while self.sock.canReadLine():
            line = bytes(self.sock.readLine())
            try:
                msg = t.agent_to_tray.validate_json(line)
            except ValidationError:
                continue  # neuere Agent-Version: unbekannte Nachrichten ignorieren
            self._dispatch(msg)

    def _dispatch(self, msg) -> None:
        if isinstance(msg, t.TrayState):
            self.stateChanged.emit(msg)
        elif isinstance(msg, t.TrayChat):
            self.historyReceived.emit(msg.messages)
        elif isinstance(msg, t.TrayChatMessage):
            self.messageReceived.emit(msg.message)
        elif isinstance(msg, t.TrayQueued):
            self.queued.emit(msg)
        elif isinstance(msg, t.TrayOpenRequestDialog):
            self.openRequestDialog.emit()
        elif isinstance(msg, t.TrayError):
            self.errorReceived.emit(msg.message)
