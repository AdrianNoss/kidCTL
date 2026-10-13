"""Fenster und Symbol der Tray-App."""

from __future__ import annotations

import html
from datetime import time

from PySide6.QtCore import QRect, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QTextBrowser, QTimeEdit, QVBoxLayout, QWidget,
)

from kidctl_common import tray as t
from kidctl_common.protocol import MAX_BODY, ChatMessage

from . import texts

COLORS = {"ok": "#27ae60", "warning": "#f39c12", "critical": "#da4453", "offline": "#7f8c8d"}


def render_icon(text: str, level: str, size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(COLORS.get(level, COLORS["offline"])))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, size - 4, size - 4)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * (0.5 if len(text) <= 2 else 0.38)))
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return QIcon(pix)


class RequestDialog(QDialog):
    """Verlängerung für heute anfragen: Minuten und/oder späteres Ende, Begründung Pflicht."""

    def __init__(self, state: t.TrayState | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Verlängerung anfordern")
        self.minutes = QSpinBox(minimum=0, maximum=180, singleStep=5, value=15, suffix=" min")
        self.later = QCheckBox("Länger aufbleiben bis")
        self.until = QTimeEdit(displayFormat="HH:mm")
        end = state.window_end if state and state.window_end else time(20, 0)
        self.until.setTime(QTime(min(end.hour + 1, 23), end.minute))
        self.until.setEnabled(False)
        self.later.toggled.connect(self.until.setEnabled)
        self.reason = QPlainTextEdit(placeholderText="Warum brauchst du mehr Zeit?")
        self.reason.setMaximumHeight(90)
        self.hint = QLabel()
        self.hint.setStyleSheet("color: #da4453")

        form = QFormLayout()
        form.addRow("Mehr Zeit:", self.minutes)
        row = QHBoxLayout()
        row.addWidget(self.later)
        row.addWidget(self.until)
        form.addRow(row)
        form.addRow("Begründung:", self.reason)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Anfragen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.hint)
        lay.addWidget(buttons)

    def request(self) -> t.TrayExtensionRequest | None:
        reason = self.reason.toPlainText().strip()
        until = self.until.time().toPython() if self.later.isChecked() else None
        if not reason:
            self.hint.setText("Bitte eine Begründung schreiben.")
            return None
        if not self.minutes.value() and until is None:
            self.hint.setText("Bitte Minuten und/oder eine spätere Uhrzeit wählen.")
            return None
        return t.TrayExtensionRequest(minutes=self.minutes.value(), window_end=until,
                                      reason=reason[:MAX_BODY])

    def _check(self) -> None:
        if self.request() is not None:
            self.accept()


class ChatWindow(QWidget):
    sendText = Signal(str)
    requestExtension = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Bildschirmzeit & Nachrichten")
        self.resize(420, 520)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.input = QLineEdit(placeholderText="Nachricht an deine Eltern …", maxLength=MAX_BODY)
        self.input.returnPressed.connect(self._send)
        send = QPushButton("Senden")
        send.clicked.connect(self._send)
        req = QPushButton("Verlängerung anfordern …")
        req.clicked.connect(self.requestExtension.emit)
        row = QHBoxLayout()
        row.addWidget(self.input)
        row.addWidget(send)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        lay.addWidget(req)
        self.messages: dict[int, ChatMessage] = {}

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.sendText.emit(text)
            self.input.clear()

    def set_state(self, state: t.TrayState | None, connected: bool) -> None:
        if not connected:
            self.status.setText("Keine Verbindung zum kidctl-Dienst.")
        elif state is not None and state.restricted:
            self.status.setText("<br>".join(html.escape(x) for x in texts.detail_lines(state)))

    def set_history(self, messages: list[ChatMessage]) -> None:
        self.messages = {m.id: m for m in messages}
        self._render()

    def upsert(self, m: ChatMessage) -> None:
        self.messages[m.id] = m
        self._render()

    def _render(self) -> None:
        parts = []
        for m in sorted(self.messages.values(), key=lambda x: x.id):
            align = "right" if m.from_child else "left"
            bg = "#d6eaf8" if m.from_child else "#eeeeee"
            body = html.escape(m.body).replace("\n", "<br>")
            if m.kind == "extension_request":
                body = (f"<b>Verlängerung: {html.escape(texts.request_summary(m))}</b><br>{body}"
                        f"<br><i>{html.escape(texts.request_status(m))}</i>")
            who = "Du" if m.from_child else html.escape(m.sender_name)
            parts.append(
                f'<table width="100%"><tr><td align="{align}">'
                f'<div style="background:{bg}; padding:6px;"><small>{who} · {texts.local_time(m.created_at)}'
                f"</small><br>{body}</div></td></tr></table>")
        self.view.setHtml("".join(parts) or "<i>Noch keine Nachrichten.</i>")
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
