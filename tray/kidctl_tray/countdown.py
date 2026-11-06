"""Letzte Minute als eigenes Fenster im Vordergrund (Windows).

„Nicht stören“ (vom Kind einschaltbar, bei Vollbildspielen automatisch) unterdrückt Toasts. Deshalb zeigt die
Tray-App die 1-Minuten-Warnung und den Countdown bis zur Abmeldung zusätzlich als Fenster, das immer oben
bleibt. Grundlage ist allein der Zustand vom Agenten (``remaining_seconds``, ``logout_in_seconds``); die
Sekunden zählt die Tray-App selbst herunter. Schließt das Kind das Fenster, erscheint es in derselben Phase
nicht erneut (Abmeldung kommt trotzdem).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from kidctl_common import tray as t

from .qt import QHBoxLayout, QLabel, QPushButton, Qt, QTimer, QVBoxLayout, QWidget, Signal
from .texts import _, ngettext

SHOW_BELOW = 60   # Sekunden


@dataclass(frozen=True)
class Due:
    kind: str          # "logout" | "warn" | "games"
    seconds: int
    can_request: bool  # „Verlängerung anfordern“ (nicht bei Sperre durch die Eltern)


class CountdownTracker:
    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.logout_at: float | None = None
        self.warn_at: float | None = None
        self.games_at: float | None = None
        self.reason: str | None = None
        self.dismissed: set[str] = set()

    def update(self, state: t.TrayState | None) -> None:
        now = self.clock()
        if state is None or not state.restricted:
            self.logout_at = self.warn_at = self.games_at = None
            self.dismissed.clear()
            return
        self.reason = state.reason
        if state.logout_in_seconds is None:
            self.logout_at = None
            self.dismissed.discard("logout")
        else:
            self.logout_at = now + state.logout_in_seconds
        if state.allowed and 0 < state.remaining_seconds <= SHOW_BELOW:
            self.warn_at = now + state.remaining_seconds
        else:
            self.warn_at = None
            self.dismissed.discard("warn")
        games_left = (state.game_limit_minutes * 60 - state.game_used_seconds
                      if state.game_limit_minutes is not None else None)
        if state.allowed and state.gaming and games_left is not None and 0 < games_left <= SHOW_BELOW:
            self.games_at = now + games_left
        else:
            self.games_at = None
            self.dismissed.discard("games")

    def due(self) -> Due | None:
        now = self.clock()
        if self.logout_at is not None and "logout" not in self.dismissed:
            left = max(0, round(self.logout_at - now))
            if left <= SHOW_BELOW:
                return Due("logout", left, self.reason != "locked")
        if self.warn_at is not None and "warn" not in self.dismissed:
            return Due("warn", max(0, round(self.warn_at - now)), True)
        if self.games_at is not None and "games" not in self.dismissed:
            return Due("games", max(0, round(self.games_at - now)), True)
        return None

    def dismiss(self, kind: str) -> None:
        self.dismissed.add(kind)


def texts_for(due: Due) -> tuple[str, str]:
    if due.kind == "games":
        return _("Spielzeit"), ngettext("Noch {n} Sekunde Spielzeit – danach werden Spiele geschlossen.",
                                        "Noch {n} Sekunden Spielzeit – danach werden Spiele geschlossen.",
                                        due.seconds).format(n=due.seconds)
    if due.kind == "warn":
        return _("Bildschirmzeit"), ngettext("Noch {n} Sekunde – danach wirst du abgemeldet.",
                                             "Noch {n} Sekunden – danach wirst du abgemeldet.", due.seconds).format(n=due.seconds)
    title = _("Gesperrt") if not due.can_request else _("Bildschirmzeit zu Ende")
    return title, ngettext("Abmeldung in {n} Sekunde – bitte jetzt speichern.",
                           "Abmeldung in {n} Sekunden – bitte jetzt speichern.", due.seconds).format(n=due.seconds)


class CountdownWindow(QWidget):
    requestExtension = Signal(str)   # "screen" | "games"

    def __init__(self, tracker: CountdownTracker | None = None) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTitleHint | Qt.WindowType.CustomizeWindowHint
                         | Qt.WindowType.WindowCloseButtonHint)
        self.tracker = tracker or CountdownTracker()
        self.setWindowTitle("KidCTL")
        self.title = QLabel()
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.title.setFont(font)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.request = QPushButton(_("Verlängerung anfordern …"))
        self.request.clicked.connect(self._request)
        ok = QPushButton(_("OK"))
        ok.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.request)
        buttons.addWidget(ok)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.text)
        layout.addLayout(buttons)
        self.setMinimumWidth(380)
        self._kind: str | None = None
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def set_state(self, state: t.TrayState | None) -> None:
        self.tracker.update(state)
        self.refresh()

    def refresh(self) -> None:
        due = self.tracker.due()
        if due is None:
            self._kind = None
            self.hide()
            return
        title, text = texts_for(due)
        self.title.setText(title)
        self.text.setText(text)
        self.request.setVisible(due.can_request)
        self.request.setText(_("Mehr Spielzeit anfragen …") if due.kind == "games" else _("Verlängerung anfordern …"))
        if self._kind != due.kind or not self.isVisible():
            self._kind = due.kind
            self.show()
            self.raise_()
            self.activateWindow()

    def _request(self) -> None:
        kind = self._kind
        self._dismiss()
        self.requestExtension.emit("games" if kind == "games" else "screen")

    def _dismiss(self) -> None:
        if self._kind:
            self.tracker.dismiss(self._kind)
        self.hide()

    def closeEvent(self, event) -> None:   # noqa: N802 (Qt)
        if self._kind:
            self.tracker.dismiss(self._kind)
        super().closeEvent(event)
