"""Tray-App: Symbol mit Restzeit, Menü, Chatfenster, Anfrage-Dialog."""

from __future__ import annotations

import sys

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from kidctl_common import tray as t

from . import texts
from .client import AgentClient
from .ui import ChatWindow, RequestDialog, render_icon


class TrayApp:
    def __init__(self, app: QApplication, socket_path: str | None = None) -> None:
        self.app = app
        self.state: t.TrayState | None = None
        self.client = AgentClient(socket_path)
        self.window = ChatWindow()
        self.tray = QSystemTrayIcon(render_icon("…", "offline"))
        self.tray.setToolTip("kidctl")

        self.menu = QMenu()
        self.status_action = QAction("Verbinde …", enabled=False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction("Chat öffnen", self.show_chat)
        self.menu.addAction("Verlängerung anfordern …", self.request_extension)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)

        c = self.client
        c.connectionChanged.connect(self._connection_changed)
        c.stateChanged.connect(self._state_changed)
        c.historyReceived.connect(self.window.set_history)
        c.messageReceived.connect(self.window.upsert)
        c.openRequestDialog.connect(self.request_extension)
        c.errorReceived.connect(lambda msg: self.tray.showMessage("kidctl", msg))
        self.window.sendText.connect(lambda text: c.send(t.TrayChatSend(body=text)))
        self.window.requestExtension.connect(self.request_extension)
        c.connect_to_agent()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_chat()

    def show_chat(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def request_extension(self) -> None:
        dlg = RequestDialog(self.state, self.window if self.window.isVisible() else None)
        if dlg.exec() and (req := dlg.request()):
            self.client.send(req)
            self.show_chat()

    def _connection_changed(self, connected: bool) -> None:
        if not connected:
            self.tray.setIcon(render_icon("…", "offline"))
            self.status_action.setText("Keine Verbindung zum kidctl-Dienst")
        self.window.set_state(self.state, connected)

    def _state_changed(self, state: t.TrayState) -> None:
        self.state = state
        if not state.restricted:  # nicht zugeordneter User: nichts anzeigen
            self.tray.hide()
            self.window.hide()
            return
        self.tray.setIcon(render_icon(texts.icon_text(state), texts.level(state)))
        line = texts.status_line(state)
        self.tray.setToolTip("\n".join(texts.detail_lines(state)))
        self.status_action.setText(line)
        self.window.set_state(state, True)
        self.tray.show()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("kidctl-tray")
    app.setDesktopFileName("kidctl-tray")
    app.setQuitOnLastWindowClosed(False)
    tray = TrayApp(app)  # Referenz halten – sonst räumt Python Objekt und Verbindungen ab
    code = app.exec()
    del tray
    return code
