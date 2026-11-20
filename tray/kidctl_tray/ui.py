"""Fenster und Symbol der Tray-App."""

from __future__ import annotations

import html
from datetime import time

from .qt import (  # noqa: F401 – PySide6 oder PyQt6
    QButtonGroup, QCheckBox, QColor, QDialog, QDialogButtonBox, QEvent, QHBoxLayout, QIcon, QLabel,
    QLineEdit, QPalette, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QTextBrowser, QTime, QTimeEdit, QVBoxLayout, QWidget, Signal,
)

from kidctl_common import tray as t
from kidctl_common.protocol import MAX_BODY, ChatMessage

from . import texts, winstyle
from .texts import _


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


def render_icon(level: str, size: int = 64) -> QIcon:
    """Tray-Symbol (Linux und Windows): monochromes KidCTL-Symbol passend zur Leiste mit Statuspunkt; die Restzeit
    steht in Tooltip und Menü."""
    return winstyle.tray_icon(level, winstyle.taskbar_light(), size)


def _buttons(dialog: QDialog, ok_text: str, on_ok) -> QDialogButtonBox:
    """„OK“ (Hauptaktion) und „Abbrechen“; die Reihenfolge folgt der Plattform."""
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    ok.setText(ok_text)
    winstyle.primary(ok)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Abbrechen"))
    buttons.accepted.connect(on_ok)
    buttons.rejected.connect(dialog.reject)
    if winstyle.active():
        buttons.setCenterButtons(False)
    return buttons


class RequestDialog(QDialog):
    """Verlängerung für heute anfragen: Minuten und/oder späteres Ende, Begründung Pflicht.
    Mit Spiele-Limit wählbar: Bildschirmzeit oder Spielzeit (nur Minuten)."""

    def __init__(self, state: t.TrayState | None, parent=None, target: str = "screen") -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Verlängerung anfordern"))
        self.screen_choice = QRadioButton(_("Bildschirmzeit"))
        self.games_choice = QRadioButton(_("Spielzeit"))
        group = QButtonGroup(self)
        group.addButton(self.screen_choice)
        group.addButton(self.games_choice)
        has_games = state is not None and state.game_limit_minutes is not None
        (self.games_choice if has_games and target == "games" else self.screen_choice).setChecked(True)
        self.minutes = QSpinBox(minimum=0, maximum=180, singleStep=5, value=15, suffix=" min")
        self.later = QCheckBox(_("Länger aufbleiben bis"))
        self.until = QTimeEdit(displayFormat="HH:mm")
        end = state.window_end if state and state.window_end else time(20, 0)
        self.until.setTime(QTime(min(end.hour + 1, 23), end.minute))
        self.until.setEnabled(False)
        self.later.toggled.connect(self.until.setEnabled)
        self.reason = QPlainTextEdit(placeholderText=_("Warum brauchst du mehr Zeit?"))
        self.reason.setMaximumHeight(90)
        self.hint = QLabel()
        self.hint.setProperty("role", "error")   # Windows: Farbe aus der Stilschicht
        if not winstyle.active():
            self.hint.setStyleSheet("color: #da4453")

        rows = []
        if has_games:
            choice = QHBoxLayout()
            choice.addWidget(self.screen_choice)
            choice.addWidget(self.games_choice)
            choice.addStretch(1)
            rows.append((_("Wofür:"), choice))
        rows.append((_("Mehr Zeit:"), self.minutes))
        row = QHBoxLayout()
        row.addWidget(self.later)
        row.addWidget(self.until)
        row.addStretch(1)
        rows.append((None, row))
        rows.append((_("Begründung:"), self.reason))
        self.games_choice.toggled.connect(self._target_changed)
        lay = QVBoxLayout(self)
        winstyle.window(self, lay)
        if head := winstyle.title(_("Verlängerung anfordern")):
            lay.addWidget(head)
        lay.addLayout(winstyle.form(rows))
        self._target_changed(self.games_choice.isChecked())
        lay.addWidget(self.hint)
        lay.addWidget(_buttons(self, _("Anfragen"), self._check))

    def _target_changed(self, games: bool) -> None:
        self.later.setVisible(not games)   # Spielzeit: nur Minuten
        self.until.setVisible(not games)

    def request(self) -> t.TrayExtensionRequest | None:
        reason = self.reason.toPlainText().strip()
        games = self.games_choice.isChecked()
        q = self.until.time()   # toPython() gibt es nur in PySide6
        until = time(q.hour(), q.minute()) if self.later.isChecked() and not games else None
        if not reason:
            self.hint.setText(_("Bitte eine Begründung schreiben."))
            return None
        if not self.minutes.value() and until is None:
            self.hint.setText(_("Bitte Minuten und/oder eine spätere Uhrzeit wählen."))
            return None
        if games and not self.minutes.value():
            self.hint.setText(_("Bitte Minuten wählen."))
            return None
        return t.TrayExtensionRequest(minutes=self.minutes.value(), window_end=until,
                                      reason=reason[:MAX_BODY], target="games" if games else "screen")

    def _check(self) -> None:
        if self.request() is not None:
            self.accept()


class AccessDialog(QDialog):
    """Freigabe für ein gesperrtes Programm bzw. eine Website anfragen; Grund optional."""

    def __init__(self, item: t.BlockedItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(_("Freigabe anfragen"))
        if winstyle.active():   # Fluent: Name als Überschrift, Art darunter
            title = QLabel(html.escape(item.name))
            title.setProperty("role", "title")
            kind = QLabel(texts.kind_label(item.access_kind))
            winstyle.secondary(kind)
            heads = [title, kind]
        else:
            heads = [QLabel(f"<b>{html.escape(item.name)}</b> ({texts.kind_label(item.access_kind)})")]
        info = QLabel(_("Deine Eltern bekommen die Anfrage und können sie dauerhaft oder nur für heute erlauben."))
        info.setWordWrap(True)
        self.reason = QPlainTextEdit(placeholderText=_("Wofür brauchst du das? (freiwillig)"))
        self.reason.setMaximumHeight(90)
        lay = QVBoxLayout(self)
        winstyle.window(self, lay)
        for w in (*heads, info, self.reason, _buttons(self, _("Anfragen"), self.accept)):
            lay.addWidget(w)

    def request(self) -> t.TrayAccessRequest:
        return t.TrayAccessRequest(key=self.item.key, reason=self.reason.toPlainText().strip()[:MAX_BODY])


class BlockedWindow(QWidget):
    """Kürzlich gesperrte Programme und Websites mit „Anfragen“ je Eintrag."""
    requestAccess = Signal(object)   # t.BlockedItem
    refresh = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_("Gesperrt – Freigabe anfragen"))
        self.resize(420, 460)
        hint = QLabel(_("Programme und Websites, die in den letzten 24 Stunden gesperrt wurden. "
                        "Websites sind je Hauptdomain zusammengefasst."))
        hint.setWordWrap(True)
        winstyle.secondary(hint)
        self.list = QVBoxLayout()
        if winstyle.active():
            self.list.setSpacing(4)
        self.list.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list)
        area = QScrollArea(widgetResizable=True)
        area.setWidget(inner)
        reload_ = QPushButton(_("Aktualisieren"))
        reload_.clicked.connect(self.refresh.emit)
        lay = QVBoxLayout(self)
        winstyle.window(self, lay)
        if head := winstyle.title(_("Gesperrt")):
            lay.addWidget(head)
        lay.addWidget(hint)
        lay.addWidget(area, 1)
        if winstyle.active():   # Fluent: Knopf rechts statt über die ganze Breite
            bottom = QHBoxLayout()
            bottom.addStretch(1)
            bottom.addWidget(reload_)
            lay.addLayout(bottom)
        else:
            lay.addWidget(reload_)
        self.items: list[t.BlockedItem] = []

    def set_items(self, items: list[t.BlockedItem]) -> None:
        self.items = items
        while self.list.count() > 1:
            w = self.list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not items:
            self.list.insertWidget(0, QLabel("<i>" + html.escape(_("In den letzten 24 Stunden wurde nichts gesperrt.")) + "</i>"))
        for i, item in enumerate(items):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(*((12, 10, 12, 10) if winstyle.active() else (0, 2, 0, 2)))
            winstyle.card(row)
            label = QLabel(f"<b>{html.escape(item.name)}</b><br><small>{html.escape(texts.blocked_line(item))}</small>")
            label.setWordWrap(True)
            button = QPushButton(_("Angefragt") if item.pending else _("Anfragen …"))
            button.setEnabled(not item.pending)
            button.clicked.connect(lambda _=False, it=item: self.requestAccess.emit(it))
            h.addWidget(label, 1)
            h.addWidget(button)
            self.list.insertWidget(i, row)


class ChatWindow(QWidget):
    sendText = Signal(str)
    requestExtension = Signal()
    showBlocked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_("Bildschirmzeit & Nachrichten"))
        self.resize(420, 520)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.input = QLineEdit(placeholderText=_("Nachricht an deine Eltern …"), maxLength=MAX_BODY)
        self.input.returnPressed.connect(self._send)
        send = QPushButton(_("Senden"))
        send.clicked.connect(self._send)
        req = QPushButton(_("Verlängerung anfordern …"))
        req.clicked.connect(self.requestExtension.emit)
        blocked = QPushButton(_("Gesperrt – Freigabe anfragen …"))
        blocked.clicked.connect(self.showBlocked.emit)
        winstyle.primary(send)
        send.setAutoDefault(False)
        row = QHBoxLayout()
        row.addWidget(self.input)
        row.addWidget(send)
        lay = QVBoxLayout(self)
        winstyle.window(self, lay)
        if head := winstyle.title(_("Bildschirmzeit & Nachrichten")):
            lay.addWidget(head)
        if winstyle.active():   # Status als Karte über dem Verlauf
            box = QWidget()
            winstyle.card(box)
            inner = QVBoxLayout(box)
            inner.setContentsMargins(14, 10, 14, 10)
            inner.addWidget(self.status)
            lay.addWidget(box)
            self.status_box = box
        else:
            lay.addWidget(self.status)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        buttons = QHBoxLayout()
        buttons.addWidget(req)
        buttons.addWidget(blocked)
        lay.addLayout(buttons)
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
            self.status.setText(_("Keine Verbindung zum kidctl-Dienst."))
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
        pad = "8px 10px" if winstyle.active() else "6px"
        if winstyle.active():   # Farben aus der Stilschicht (Palette folgt dem Stylesheet nicht)
            tok = winstyle.STYLE.tokens
            own, other, fg = _mix(QColor(tok["card"]), QColor(tok["accent"]), 0.22), tok["control"], tok["text"]
        for m in sorted(self.messages.values(), key=lambda x: x.id):
            align = "right" if m.from_child else "left"
            bg = own if m.from_child else other
            body = html.escape(m.body).replace("\n", "<br>")
            if m.kind == "extension_request":
                label = _("Spielzeit") if m.request_target == "games" else _("Verlängerung")
                body = (f"<b>{html.escape(label)}: {html.escape(texts.request_summary(m))}</b><br>{body}"
                        f"<br><i>{html.escape(texts.request_status(m))}</i>")
            elif m.kind == "access_request":
                what = f"{texts.kind_label(m.access_kind)} {m.access_name or ''}"
                body = (f"<b>{html.escape(_('Freigabe:'))} {html.escape(what)}</b>" + (f"<br>{body}" if body else "")
                        + f"<br><i>{html.escape(texts.access_status(m))}</i>")
            who = html.escape(_("Du")) if m.from_child else html.escape(m.sender_name)
            parts.append(
                f'<table width="100%"><tr><td align="{align}">'
                f'<div style="background:{bg}; color:{fg}; padding:{pad};"><small>{who} · {texts.local_time(m.created_at)}'
                f"</small><br>{body}</div></td></tr></table>")
        self.view.setHtml("".join(parts) or "<i>" + html.escape(_("Noch keine Nachrichten.")) + "</i>")
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
