"""Tray-App: Symbol mit Restzeit, Menü, Chatfenster, Anfrage-Dialog.

``kidctl-tray --show`` (Eintrag „KidCTL“ in der GNOME-Anwendungsübersicht) öffnet das Fenster; läuft die
Tray-App schon, bittet es sie darüber über einen Socket in ``$XDG_RUNTIME_DIR`` (nur eine Instanz je User).
GNOME zeigt das Tray-Symbol nur mit der AppIndicator-Erweiterung – ob es sichtbar ist, meldet die App dem
Agenten (Integritätsprüfung).
"""

from __future__ import annotations

import os
import sys


from kidctl_common import tray as t

from .qt import QAction, QApplication, QLocalServer, QLocalSocket, QMenu, QMessageBox, QSystemTrayIcon, QTimer

from . import texts
from .texts import _
from .client import AgentClient
from .ui import AccessDialog, BlockedWindow, ChatWindow, RequestDialog, render_icon

ICON_CHECK_MS = 30_000   # Sichtbarkeit des Tray-Symbols prüfen (Erweiterung ein-/ausgeschaltet)
INSTANCE_SOCKET = "kidctl-tray.sock"


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
        self.status_action = QAction(_("Verbinde …"), enabled=False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction(_("Chat öffnen"), self.show_chat)
        self.menu.addAction(_("Verlängerung anfordern …"), lambda: self.request_extension())
        self.menu.addAction(_("Gesperrt – Freigabe anfragen …"), self.show_blocked)
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
        c.errorReceived.connect(self.info)
        self.window.sendText.connect(lambda text: c.send(t.TrayChatSend(body=text)))
        self.window.requestExtension.connect(lambda: self.request_extension())
        self.window.showBlocked.connect(self.show_blocked)
        c.set_tray_icon(QSystemTrayIcon.isSystemTrayAvailable())
        self._icon_timer = QTimer(interval=ICON_CHECK_MS)
        self._icon_timer.timeout.connect(lambda: c.set_tray_icon(QSystemTrayIcon.isSystemTrayAvailable()))
        self._icon_timer.start()
        c.connect_to_agent()

    def info(self, text: str) -> None:
        """Kurzer Hinweis: als Tray-Meldung, ohne sichtbares Tray-Symbol als Dialog."""
        if self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.showMessage("KidCTL", text)
        else:
            QMessageBox.information(self.window if self.window.isVisible() else None, "KidCTL", text)

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
            self.info(_("„{name}“ ist schon angefragt.").format(name=item.name))
            return
        parent = self.blocked if self.blocked.isVisible() else None
        dlg = AccessDialog(item, parent)
        if dlg.exec():
            self.client.send(dlg.request())
            self.show_chat()

    def _connection_changed(self, connected: bool) -> None:
        if not connected:
            self.tray.setIcon(render_icon("…", "offline"))
            self.status_action.setText(_("Keine Verbindung zum kidctl-Dienst"))
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


def instance_path() -> str | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime, INSTANCE_SOCKET) if runtime else None


def ask_running_instance(path: str) -> bool:
    """Laufende Tray-App bitten, ihr Fenster zu zeigen. False = keine läuft."""
    sock = QLocalSocket()
    sock.connectToServer(path)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def serve_instance(path: str, tray: TrayApp) -> QLocalServer:
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(path)   # verwaister Socket einer abgestürzten Instanz
    server.listen(path)

    def on_connection() -> None:
        while (conn := server.nextPendingConnection()) is not None:
            conn.readyRead.connect(lambda c=conn: c.readAll() and tray.show_chat())
            conn.disconnected.connect(conn.deleteLater)
    server.newConnection.connect(on_connection)
    return server


def main() -> int:
    texts.setup_language()   # vor dem Aufbau der Fenster
    show = "--show" in sys.argv[1:]
    app = QApplication(sys.argv)
    app.setApplicationName("kidctl-tray")
    # GNOME: Fenster dem sichtbaren Eintrag „KidCTL“ zuordnen (Symbol im Dock, anheftbar)
    gnome = "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    app.setDesktopFileName("kidctl" if gnome else "kidctl-tray")
    app.setQuitOnLastWindowClosed(False)
    path = instance_path()
    if path and ask_running_instance(path):
        return 0   # läuft schon – Fenster dort geöffnet
    tray = TrayApp(app)  # Referenz halten – sonst räumt Python Objekt und Verbindungen ab
    server = serve_instance(path, tray) if path else None
    if show:
        tray.show_chat()
    code = app.exec()
    del tray, server
    return code
