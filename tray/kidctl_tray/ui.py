"""Fenster und Symbol der Tray-App."""

from __future__ import annotations

import html
from datetime import time

from PySide6.QtCore import QEvent, QRect, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTextBrowser, QTimeEdit, QVBoxLayout, QWidget,
)

from kidctl_common import tray as t
from kidctl_common.protocol import MAX_BODY, ChatMessage

from . import texts

COLORS = {"ok": "#27ae60", "warning": "#f39c12", "critical": "#da4453", "offline": "#7f8c8d"}


def _mix(a: QColor, b: QColor, share_b: float) -> str:
    """Farbe zwischen a und b (share_b = Anteil von b) als #rrggbb."""
    return QColor(*(round(x * (1 - share_b) + y * share_b) for x, y in
                    zip(a.getRgb()[:3], b.getRgb()[:3]))).name()


def bubble_colors(pal: QPalette) -> tuple[str, str, str]:
    """(eigene Blase, fremde Blase, Schrift) aus der Systempalette – lesbar in hellen und dunklen
    Themes (z. B. Breeze Dark), weil Hintergrund und Schrift aus derselben Palette stammen."""
    base, text = pal.color(QPalette.ColorRole.Base), pal.color(QPalette.ColorRole.Text)
    own = _mix(base, pal.color(QPalette.ColorRole.Highlight), 0.25)
    other = _mix(base, text, 0.08)
    return own, other, text.name()


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


class AccessDialog(QDialog):
    """Freigabe für ein gesperrtes Programm bzw. eine Website anfragen; Grund optional."""

    def __init__(self, item: t.BlockedItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setWindowTitle("Freigabe anfragen")
        title = QLabel(f"<b>{html.escape(item.name)}</b> ({texts.kind_label(item.access_kind)})")
        info = QLabel("Deine Eltern bekommen die Anfrage und können sie dauerhaft oder nur für heute erlauben.")
        info.setWordWrap(True)
        self.reason = QPlainTextEdit(placeholderText="Wofür brauchst du das? (freiwillig)")
        self.reason.setMaximumHeight(90)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Anfragen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (title, info, self.reason, buttons):
            lay.addWidget(w)

    def request(self) -> t.TrayAccessRequest:
        return t.TrayAccessRequest(key=self.item.key, reason=self.reason.toPlainText().strip()[:MAX_BODY])


class BlockedWindow(QWidget):
    """Kürzlich gesperrte Programme und Websites mit „Anfragen“ je Eintrag."""
    requestAccess = Signal(object)   # t.BlockedItem
    refresh = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Gesperrt – Freigabe anfragen")
        self.resize(420, 460)
        hint = QLabel("Programme und Websites, die in den letzten 24 Stunden gesperrt wurden. "
                      "Websites sind je Hauptdomain zusammengefasst.")
        hint.setWordWrap(True)
        self.list = QVBoxLayout()
        self.list.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list)
        area = QScrollArea(widgetResizable=True)
        area.setWidget(inner)
        reload_ = QPushButton("Aktualisieren")
        reload_.clicked.connect(self.refresh.emit)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(area, 1)
        lay.addWidget(reload_)
        self.items: list[t.BlockedItem] = []

    def set_items(self, items: list[t.BlockedItem]) -> None:
        self.items = items
        while self.list.count() > 1:
            w = self.list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not items:
            self.list.insertWidget(0, QLabel("<i>In den letzten 24 Stunden wurde nichts gesperrt.</i>"))
        for i, item in enumerate(items):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 2, 0, 2)
            label = QLabel(f"<b>{html.escape(item.name)}</b><br><small>{html.escape(texts.blocked_line(item))}</small>")
            label.setWordWrap(True)
            button = QPushButton("Angefragt" if item.pending else "Anfragen …")
            button.setEnabled(not item.pending)
            button.clicked.connect(lambda _=False, it=item: self.requestAccess.emit(it))
            h.addWidget(label, 1)
            h.addWidget(button)
            self.list.insertWidget(i, row)


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

    def changeEvent(self, event) -> None:  # Farbschema gewechselt -> Blasen neu einfärben
        if event.type() == QEvent.Type.PaletteChange:
            self._render()
        super().changeEvent(event)

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
        own, other, fg = bubble_colors(self.view.palette())
        for m in sorted(self.messages.values(), key=lambda x: x.id):
            align = "right" if m.from_child else "left"
            bg = own if m.from_child else other
            body = html.escape(m.body).replace("\n", "<br>")
            if m.kind == "extension_request":
                body = (f"<b>Verlängerung: {html.escape(texts.request_summary(m))}</b><br>{body}"
                        f"<br><i>{html.escape(texts.request_status(m))}</i>")
            elif m.kind == "access_request":
                what = f"{texts.kind_label(m.access_kind)} {m.access_name or ''}"
                body = (f"<b>Freigabe: {html.escape(what)}</b>" + (f"<br>{body}" if body else "")
                        + f"<br><i>{html.escape(texts.access_status(m))}</i>")
            who = "Du" if m.from_child else html.escape(m.sender_name)
            parts.append(
                f'<table width="100%"><tr><td align="{align}">'
                f'<div style="background:{bg}; color:{fg}; padding:6px;"><small>{who} · {texts.local_time(m.created_at)}'
                f"</small><br>{body}</div></td></tr></table>")
        self.view.setHtml("".join(parts) or "<i>Noch keine Nachrichten.</i>")
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
