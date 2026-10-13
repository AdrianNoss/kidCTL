"""GUI-Smoke-Test (offscreen) gegen einen Fake-Agenten auf einem lokalen Socket."""

import json
from datetime import datetime, time, timezone

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QCoreApplication, QElapsedTimer  # noqa: E402
from PySide6.QtNetwork import QLocalServer  # noqa: E402

from kidctl_common import tray as t  # noqa: E402
from kidctl_common.protocol import ChatMessage  # noqa: E402

from kidctl_tray import app as app_mod  # noqa: E402


def spin(cond, ms=3000):
    timer = QElapsedTimer()
    timer.start()
    while not cond() and timer.elapsed() < ms:
        QCoreApplication.processEvents()
    return cond()


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeAgent:
    def __init__(self, path):
        self.server = QLocalServer()
        assert self.server.listen(path)
        self.conn = None
        self.lines = []
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        self.conn = self.server.nextPendingConnection()
        self.conn.readyRead.connect(self._read)

    def _read(self):
        while self.conn.canReadLine():
            self.lines.append(json.loads(bytes(self.conn.readLine())))

    def push(self, msg):
        self.conn.write(msg.model_dump_json().encode() + b"\n")
        self.conn.flush()


def msg(mid, **kw):
    return ChatMessage(id=mid, child_id=1, from_child=kw.pop("from_child", False), sender_name="Mama",
                       kind=kw.pop("kind", "text"), body=kw.pop("body", f"Text {mid}"),
                       created_at=datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc), **kw)


def test_tray_flow(qapp, tmp_path, monkeypatch):
    agent = FakeAgent(str(tmp_path / "tray.sock"))
    tray = app_mod.TrayApp(qapp, str(tmp_path / "tray.sock"))
    assert spin(lambda: agent.lines)               # hello
    assert agent.lines[0] == {"type": "hello", "version": t.TRAY_PROTOCOL_VERSION}

    agent.push(t.TrayState(restricted=True, child_name="Max", allowed=True, remaining_seconds=12 * 60,
                           used_seconds=48 * 60, allowed_minutes=60, window_start=time(8),
                           window_end=time(20), online=True))
    agent.push(t.TrayChat(messages=[msg(1, body="<b>Hallo</b>")]))
    assert spin(lambda: tray.state is not None and tray.window.messages)
    assert tray.status_action.text() == "Noch 12 min (heute bis 20:00 Uhr)"
    # HTML in Nachrichten wird nicht interpretiert, sondern wörtlich angezeigt
    assert "<b>Hallo</b>" in tray.window.view.toPlainText()

    # Senden aus dem Chatfenster
    tray.window.input.setText("Darf ich länger?")
    tray.window._send()
    assert spin(lambda: len(agent.lines) == 2)
    assert agent.lines[1] == {"type": "chat_send", "body": "Darf ich länger?"}

    # Agent fordert Anfrage-Dialog an (Klick in Benachrichtigung) -> Dialog -> extension_request
    def fake_exec(self):
        self.minutes.setValue(30)
        self.reason.setPlainText("Referat")
        return True
    monkeypatch.setattr(app_mod.RequestDialog, "exec", fake_exec)
    agent.push(t.TrayOpenRequestDialog())
    assert spin(lambda: len(agent.lines) == 3)
    assert agent.lines[2] == {"type": "extension_request", "minutes": 30, "window_end": None, "reason": "Referat"}

    # Aktualisierte Anfrage erscheint im Verlauf
    agent.push(t.TrayChatMessage(message=msg(2, from_child=True, kind="extension_request", body="Referat",
                                             requested_minutes=30, status="approved", approved_minutes=20)))
    assert spin(lambda: 2 in tray.window.messages)
    assert "Genehmigt: +20 min" in tray.window.view.toPlainText()

    # Nicht zugeordneter User: Symbol aus
    agent.push(t.TrayState(restricted=False))
    assert spin(lambda: not tray.state.restricted)
    assert not tray.tray.isVisible()


def test_request_dialog_validation(qapp):
    from kidctl_tray.ui import RequestDialog
    d = RequestDialog(None)
    d.minutes.setValue(0)
    assert d.request() is None and "Begründung" in d.hint.text()
    d.reason.setPlainText("Grund")
    assert d.request() is None and "Minuten" in d.hint.text()
    d.later.setChecked(True)
    r = d.request()
    assert r.minutes == 0 and r.window_end == time(21, 0)
