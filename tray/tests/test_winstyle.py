"""Windows-Stilschicht (offscreen): Kontrastprüfung, Farbauswahl, Stylesheet nur unter Windows."""

import pytest

from kidctl_tray import winstyle as ws

qt = pytest.importorskip("kidctl_tray.qt")


def test_contrast_and_accent_choice():
    assert round(ws.contrast("#ffffff", "#000000")) == 21 and ws.contrast("#777777", "#777777") == 1
    assert ws.pick_accent("#0078D4") == ("#0078d4", "#ffffff")          # Windows-Standardblau, hell: weiße Schrift
    assert ws.pick_accent("#ffb900") == (ws.KIDCTL_BLUE, "#ffffff")     # helles Gelb: zu wenig Kontrast
    dark_acc, fg = ws.pick_accent("#0078d4", dark=True)                 # dunkel: hellere Stufe, schwarze Schrift
    assert fg == "#000000" and dark_acc != "#0078d4" and ws.contrast(dark_acc, fg) >= ws.MIN_CONTRAST
    assert ws.pick_accent("#000000", dark=True) == (ws.KIDCTL_BLUE_DARK, "#000000")   # Schwarz: Rückfall
    assert ws.pick_accent(None) == (ws.KIDCTL_BLUE, "#ffffff")
    for dark in (False, True):
        t = ws.tokens(dark, "#0078d4")
        assert ws.contrast(t["accent"], t["on_accent"]) >= ws.MIN_CONTRAST
        assert ws.contrast(t["text"], t["window"]) >= 7
        for level in ("ok", "warning", "critical", "offline"):       # Statusfarben lesbar auf der Karte
            assert ws.contrast(ws.status_color(level, dark), t["card"]) >= 3, (dark, level)


def test_stylesheet_only_on_windows():
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    before = app.styleSheet()
    assert ws.apply(app, platform="linux") is False and app.styleSheet() == before   # Linux: nativer Look
    assert not ws.active("linux") and ws.active("win32")
    qss = ws.stylesheet(ws.tokens(False, "#0078d4"))
    assert "#0078d4" in qss and "border-radius: 8px" in qss and "border-radius: 4px" in qss
    assert "#da4453" not in qss


def test_all_windows_build_in_both_schemes():
    from kidctl_common import tray as t
    from kidctl_tray import ui
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    try:
        for dark in (False, True):
            app.setStyleSheet(ws.stylesheet(ws.tokens(dark, "#0078d4")))
            for w in (ui.ChatWindow(), ui.BlockedWindow(), ui.RequestDialog(None),
                      ui.AccessDialog(t.BlockedItem(access_kind="program", key="exe:x", name="X",
                                                    last_at="2026-11-19T10:00:00Z", count=1))):
                w.show()
                qt.QCoreApplication.processEvents()
                w.close()
    finally:
        app.setStyleSheet("")


@pytest.fixture
def win_style(monkeypatch):
    """Windows-Stil offscreen erzwingen (Bausteine, Stylesheet)."""
    app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    monkeypatch.setattr(ws, "FORCE", True)
    yield app
    app.setStyleSheet("")


def test_linux_keeps_native_layout():
    from kidctl_tray import ui
    assert ws.title("x") is None                                        # Überschrift nur in der Titelleiste
    form = ws.form([("Mehr Zeit:", qt.QtWidgets.QSpinBox())])
    assert isinstance(form, qt.QtWidgets.QFormLayout)
    d = ui.RequestDialog(None)
    assert not d.property("kidctlWindow")
    img = ui.render_icon("critical", 64).pixmap(64, 64).toImage()                      # Linux: Symbol wie Windows
    assert img.pixelColor(51, 51).name() == ws.status_color("critical", not ws.taskbar_light())


def test_windows_layout_and_icon(win_style):
    from kidctl_common import tray as t
    from kidctl_tray import ui
    from kidctl_tray.countdown import CountdownTracker, CountdownWindow
    for dark in (False, True):
        win_style.setStyleSheet(ws.stylesheet(ws.tokens(dark, "#0078d4")))
        d = ui.RequestDialog(t.TrayState(restricted=True, game_limit_minutes=60))
        assert d.property("kidctlWindow")
        labels = [w.text() for w in d.findChildren(qt.QtWidgets.QLabel)]
        assert "Verlängerung anfordern" in labels and "Begründung" in labels      # Überschrift, Feld über Eingabe
        ok = [b for b in d.findChildren(qt.QtWidgets.QPushButton) if b.text() == "Anfragen"][0]
        assert ok.property("primary")
        chat = ui.ChatWindow()
        chat.set_history([])
        assert chat.status_box.property("card")
        blocked = ui.BlockedWindow()
        blocked.set_items([t.BlockedItem(access_kind="program", key="exe:x", name="X",
                                         last_at="2026-11-19T10:00:00Z", count=1)])
        assert any(w.property("card") for w in blocked.findChildren(qt.QtWidgets.QWidget))
        cd = CountdownWindow(CountdownTracker(clock=lambda: 0.0))
        cd.set_state(t.TrayState(restricted=True, allowed=False, reason="quota", logout_in_seconds=45))
        assert cd.big.text() == "0:45" and cd.request.property("primary")
        for w in (d, chat, blocked, cd):
            w.close()
    # Tray-Symbol: monochrom passend zur Taskleiste, Statuspunkt in der Statusfarbe
    for light in (False, True):
        img = ws.tray_icon("critical", light, 64).pixmap(64, 64).toImage()
        assert img.pixelColor(51, 51).name() == ws.status_color("critical", not light)
        assert img.pixelColor(31, 56).name() == ("#1b1b1b" if light else "#ffffff")   # Zifferblatt unten


def test_toast_with_logo(tmp_path, win_style):
    from kidctl_tray.winplatform import toast_xml
    path = ws.logo_png(str(tmp_path / "logo.png"))
    assert path and (tmp_path / "logo.png").read_bytes()[:4] == b"\x89PNG"
    xml = toast_xml("T", "B", "ab12", "Anfragen", False, r"C:\Users\kind\AppData\Local\KidCTL\toast-logo.png")
    assert '<image placement="appLogoOverride" src="file:///C:/Users/kind/AppData/Local/KidCTL/toast-logo.png"/>' in xml
    assert "<image" not in toast_xml("T", "B", None, "", False)


def test_window_hook_ignores_non_widgets(win_style):
    """Der Show-Hook sieht auch QWindow-Objekte (ohne isWindow) – darf nicht abstürzen."""
    from kidctl_tray import ui
    ws._install_window_hook(win_style)
    try:
        d = ui.RequestDialog(None)
        d.show()
        qt.QCoreApplication.processEvents()
        d.windowHandle().show()                     # QWindow-Show-Ereignis
        qt.QCoreApplication.processEvents()
        d.close()
    finally:
        win_style.removeEventFilter(win_style._kidctl_hook)


def test_linux_panel_brightness(monkeypatch):
    """GNOME vor 51: dunkle Leiste (helles Symbol); GNOME ab 51 und Plasma: nach Farbschema."""
    qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])
    monkeypatch.setattr(ws.sys, "platform", "linux")
    hints = qt.QtWidgets.QApplication.instance().styleHints()
    monkeypatch.setattr(type(hints), "colorScheme", lambda self: qt.Qt.ColorScheme.Light)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setattr(ws, "gnome_major", lambda: 46)
    assert ws.taskbar_light() is False                                   # GNOME 46 (Ubuntu 24.04): Leiste immer dunkel
    monkeypatch.setattr(ws, "gnome_major", lambda: 51)
    assert ws.taskbar_light() is True                                    # GNOME 51: folgt dem Farbschema
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    for scheme, light in ((qt.Qt.ColorScheme.Light, True), (qt.Qt.ColorScheme.Dark, False)):
        monkeypatch.setattr(type(hints), "colorScheme", lambda self, s=scheme: s)
        assert ws.taskbar_light() is light
