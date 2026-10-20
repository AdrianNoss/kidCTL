"""Tray-App: Symbol mit Restzeit, Menü, Chatfenster, Anfrage-Dialog."""

from __future__ import annotations

import sys

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from kidctl_common import tray as t

from . import texts
from .client import AgentClient
from .ui import AccessDialog, BlockedWindow, ChatWindow, RequestDialog, render_icon


class TrayApp:
    def __init__(self, app: QApplication, socket_path: str | None = None) -> None:
        self.app = app
        self.state: t.TrayState | None = None
        self.client = AgentClient(socket_path)
        self.window = ChatWindow()
        self.blocked = BlockedWindow()
        self.tray = QSystemTrayIcon(render_icon("…", "offline"))
        self.tray.setToolTip("KidCTL")

        self.menu = QMenu()
        self.status_action = QAction("Verbinde …", enabled=False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction("Chat öffnen", self.show_chat)
        self.menu.addAction("Verlängerung anfordern …", lambda: self.request_extension())
        self.menu.addAction("Gesperrt – Freigabe anfragen …", self.show_blocked)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)

        c = self.client
        c.connectionChanged.connect(self._connection_changed)
        c.stateChanged.connect(self._state_changed)
        c.historyReceived.connect(self.window.set_history)
        c.messageReceived.connect(self.window.upsert)
        c.openRequestDialog.connect(self.request_extension)
        c.blockedReceived.connect(self.blocked.set_items)
        c.openAccessDialog.connect(self.request_access)
        self.blocked.requestAccess.connect(self.request_access)
        self.blocked.refresh.connect(lambda: c.send(t.TrayGetBlocked()))
        c.errorReceived.connect(lambda msg: self.tray.showMessage("KidCTL", msg))
        self.window.sendText.connect(lambda text: c.send(t.TrayChatSend(body=text)))
        self.window.requestExtension.connect(lambda: self.request_extension())
        c.connect_to_agent()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_chat()

    def show_chat(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def request_extension(self, target: str = "screen") -> None:
        dlg = RequestDialog(self.state, self.window if self.window.isVisible() else None, target)
        if dlg.exec() and (req := dlg.request()):
            self.client.send(req)
            self.show_chat()

    def show_blocked(self) -> None:
        self.client.send(t.TrayGetBlocked())
        self.blocked.show()
        self.blocked.raise_()
        self.blocked.activateWindow()

    def request_access(self, item: t.BlockedItem) -> None:
        if item.pending:
            self.tray.showMessage("KidCTL", f"„{item.name}“ ist schon angefragt.")
            return
        parent = self.blocked if self.blocked.isVisible() else None
        dlg = AccessDialog(item, parent)
        if dlg.exec():
            self.client.send(dlg.request())
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
            self.blocked.hide()
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
