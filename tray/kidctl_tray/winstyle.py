"""Windows-Stilschicht der Tray-App (Windows 10/11 im Fluent-Stil). Nur unter Windows geladen – Linux behält den
nativen Look (Breeze/Adwaita); Logik, Texte und Übersetzungen bleiben gemeinsam.

- Farben aus Fluent-Tönen je hell/dunkel (Windows-Einstellung, live umschaltend), Statusfarben fest.
- Akzentfarbe des Benutzers (Registry ``DWM\\AccentColor``); Text darauf (hell weiß, dunkel schwarz auf einer
  helleren Stufe) muss ≥ 4,5:1 Kontrast haben, sonst KidCTL-Blau.
- Stylesheet (QSS): Segoe UI Variable, Ecken 8 px (Fenster/Karten) bzw. 4 px (Eingaben/Buttons), primärer Button
  gefüllt, sekundäre umrandet, Eingaben mit Akzent-Unterstrich im Fokus.
- Mica unter Windows 11 (DWM) für den Fensterrahmen, Fläche in der Fluent-Grundfarbe; Windows 10 einfarbig.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("kidctl.tray")

KIDCTL_BLUE = "#3b5ba9"
KIDCTL_BLUE_DARK = "#b1c5ff"
MIN_CONTRAST = 4.5
FONT = "Segoe UI Variable Text"
FONT_FALLBACK = "Segoe UI"

# Fluent-Töne (WinUI 3) – Flächen, Linien, Schrift
BASE = {
    False: {"window": "#f3f3f3", "card": "#fbfbfb", "control": "#ffffff", "control_hover": "#f9f9f9",
            "control_pressed": "#f5f5f5", "stroke": "#e5e5e5", "stroke_strong": "#8a8a8a", "text": "#1b1b1b",
            "text2": "#5f5f5f", "disabled": "#a0a0a0", "selection": "#e6e6e6", "divider": "#ebebeb"},
    True: {"window": "#202020", "card": "#2b2b2b", "control": "#2d2d2d", "control_hover": "#323232",
           "control_pressed": "#272727", "stroke": "#3a3a3a", "stroke_strong": "#9a9a9a", "text": "#ffffff",
           "text2": "#c5c5c5", "disabled": "#787878", "selection": "#3d3d3d", "divider": "#353535"},
}
# Statusfarben (ok/Warnung/kritisch/offline) – fest, je Schema lesbar auf der Fläche
STATUS = {
    False: {"ok": "#0f7b0f", "warning": "#9d5d00", "critical": "#c42b1c", "offline": "#6b6b6b"},
    True: {"ok": "#6ccb5f", "warning": "#fce100", "critical": "#ff99a4", "offline": "#9d9d9d"},
}


# ---------- Farben ----------

def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def luminance(color: str) -> float:
    def ch(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """Kontrastverhältnis nach WCAG 2.x (1 … 21)."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def pick_accent(accent: str | None, dark: bool = False) -> tuple[str, str]:
    """(Akzent, Schrift darauf) wie Fluent: hell weiße Schrift auf dem Akzent, dunkel schwarze Schrift auf einer
    helleren Akzentstufe. Unter 4,5:1 Kontrast: KidCTL-Blau (hell #3b5ba9, dunkel #b1c5ff)."""
    fg = "#000000" if dark else "#ffffff"
    if accent:
        color = _shade(accent.lower(), 1.45) if dark else accent.lower()
        if contrast(color, fg) >= MIN_CONTRAST:
            return color, fg
    return (KIDCTL_BLUE_DARK if dark else KIDCTL_BLUE), fg


def _shade(color: str, factor: float) -> str:
    """Heller (>1) bzw. dunkler (<1) – für Hover/Gedrückt des primären Buttons."""
    r, g, b = _rgb(color)
    if factor >= 1:
        return "#%02x%02x%02x" % tuple(round(v + (255 - v) * (factor - 1)) for v in (r, g, b))
    return "#%02x%02x%02x" % tuple(round(v * factor) for v in (r, g, b))


def tokens(dark: bool, accent: str | None = None) -> dict[str, str]:
    acc, on_acc = pick_accent(accent, dark)
    tok = dict(BASE[dark])
    tok.update(accent=acc, on_accent=on_acc, accent_hover=_shade(acc, 1.12 if dark else 0.92),
               accent_pressed=_shade(acc, 0.85 if dark else 0.84),
               **{f"status_{k}": v for k, v in STATUS[dark].items()})
    return tok


def status_color(level: str, dark: bool) -> str:
    return STATUS[dark].get(level, STATUS[dark]["offline"])


# ---------- Stylesheet ----------

def stylesheet(t: dict[str, str]) -> str:
    """QSS für alle Fenster der Tray-App. Primäre Buttons: ``QPushButton[primary="true"]`` oder Standardknopf.
    Radio-/Kontrollkästchen und Zahlen-/Zeitfelder bleiben beim nativen Qt-Stil „windows11“ (Fluent, Akzentfarbe) –
    ein Stylesheet darauf schaltet dessen Zeichnung ab."""
    return f"""
* {{ font-family: "{FONT}", "{FONT_FALLBACK}"; font-size: 10pt; color: {t['text']}; }}
QWidget#kidctlWindow, QDialog, QWidget[kidctlWindow="true"] {{ background: {t['window']}; }}
QFrame[card="true"], QWidget[card="true"] {{ background: {t['card']}; border: 1px solid {t['stroke']};
    border-radius: 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 14pt; font-weight: 600; }}
QLabel[role="subtitle"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="secondary"] {{ color: {t['text2']}; }}
QLabel[role="error"] {{ color: {t['status_critical']}; }}
QLabel[role="countdown"] {{ font-size: 28pt; font-weight: 600; }}
QLabel[role="field"] {{ color: {t['text']}; padding-top: 4px; }}
QLabel[role="status"] {{ color: {t['text2']}; }}
QPushButton {{ background: {t['control']}; border: 1px solid {t['stroke']}; border-bottom-color: {t['stroke_strong']};
    border-radius: 4px; padding: 6px 16px; min-height: 20px; }}
QPushButton:hover {{ background: {t['control_hover']}; }}
QPushButton:pressed {{ background: {t['control_pressed']}; color: {t['text2']}; }}
QPushButton:disabled {{ color: {t['disabled']}; }}
QPushButton[primary="true"], QPushButton:default {{ background: {t['accent']}; color: {t['on_accent']};
    border: 1px solid {t['accent']}; }}
QPushButton[primary="true"]:hover, QPushButton:default:hover {{ background: {t['accent_hover']}; }}
QPushButton[primary="true"]:pressed, QPushButton:default:pressed {{ background: {t['accent_pressed']}; }}
QLineEdit, QPlainTextEdit, QTextBrowser {{ background: {t['control']};
    border: 1px solid {t['stroke']}; border-bottom: 1px solid {t['stroke_strong']}; border-radius: 4px;
    padding: 5px 8px; selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-bottom: 2px solid {t['accent']}; }}
QTextBrowser {{ background: {t['card']}; border-radius: 8px; border-bottom: 1px solid {t['stroke']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['stroke_strong']}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenu {{ background: {t['card']}; border: 1px solid {t['stroke']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 4px; margin: 1px 2px; }}
QMenu::item:selected {{ background: {t['selection']}; }}
QMenu::item:disabled {{ color: {t['text2']}; }}
QMenu::separator {{ height: 1px; background: {t['divider']}; margin: 4px 8px; }}
QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['stroke']}; border-radius: 4px;
    padding: 4px 8px; }}
"""


def palette(t: dict[str, str]):
    """Qt-Palette aus den Farbwerten – damit native Steuerelemente (Stil „windows11“) zum Stylesheet passen."""
    from .qt import QColor, QPalette
    pal = QPalette()
    R = QPalette.ColorRole
    for role, key in ((R.Window, "window"), (R.Base, "control"), (R.AlternateBase, "card"), (R.Button, "control"),
                      (R.Text, "text"), (R.WindowText, "text"), (R.ButtonText, "text"), (R.PlaceholderText, "text2"),
                      (R.Highlight, "accent"), (R.HighlightedText, "on_accent"), (R.ToolTipBase, "card"),
                      (R.ToolTipText, "text"), (R.Mid, "stroke"), (R.Dark, "stroke_strong")):
        pal.setColor(role, QColor(t[key]))
    if hasattr(R, "Accent"):   # Qt ≥ 6.6
        pal.setColor(R.Accent, QColor(t["accent"]))
    pal.setColor(QPalette.ColorGroup.Disabled, R.Text, QColor(t["disabled"]))
    pal.setColor(QPalette.ColorGroup.Disabled, R.ButtonText, QColor(t["disabled"]))
    return pal


# ---------- Windows: Akzent, Schema, Mica ----------

def read_accent() -> str | None:
    """Akzentfarbe des Benutzers (``HKCU\\Software\\Microsoft\\Windows\\DWM\\AccentColor``, ABGR) als #rrggbb."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            value = int(winreg.QueryValueEx(k, "AccentColor")[0]) & 0xFFFFFFFF
    except (ImportError, OSError, ValueError):
        return None
    r, g, b = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
    return "#%02x%02x%02x" % (r, g, b)


def system_dark(app=None) -> bool:
    """Dunkles App-Schema laut Windows (Qt ≥ 6.5 über styleHints, sonst Registry)."""
    try:
        from .qt import Qt
        scheme = (app or _app()).styleHints().colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            return scheme == Qt.ColorScheme.Dark
    except (AttributeError, RuntimeError):
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return int(winreg.QueryValueEx(k, "AppsUseLightTheme")[0]) == 0
    except (ImportError, OSError, ValueError):
        return False


def _app():
    from .qt import QApplication
    return QApplication.instance()


def windows_build() -> int:
    try:
        return sys.getwindowsversion().build
    except AttributeError:
        return 0


def mica_available() -> bool:
    return sys.platform == "win32" and windows_build() >= 22621   # Windows 11 22H2: DWMWA_SYSTEMBACKDROP_TYPE


class Style:
    """Aktuelles Schema der Tray-App unter Windows; ``apply`` setzt Stylesheet und hält es aktuell."""

    def __init__(self) -> None:
        self.dark = False
        self.accent: str | None = None
        self.tokens = tokens(False)
        self.listeners: list = []   # z. B. Tray-Symbol neu zeichnen

    def refresh(self, app) -> None:
        self.dark, self.accent = system_dark(app), read_accent()
        self.tokens = tokens(self.dark, self.accent)
        self.set(app)

    def set(self, app) -> None:
        """Palette und Stylesheet aus ``self.tokens`` setzen."""
        app.setPalette(palette(self.tokens))
        app.setStyleSheet(stylesheet(self.tokens))
        for w in app.topLevelWidgets():
            if w.property("kidctlWindow") and w.isVisible():
                enable_mica(w, self.dark)
        for callback in list(self.listeners):
            callback()


STYLE = Style()


FORCE = False   # Tests/Screenshots: Windows-Stil auch anderswo (dev/winshots.py nutzt ihn unter Windows)


def active(platform: str | None = None) -> bool:
    return FORCE if platform is None and FORCE else (platform or sys.platform) == "win32"


# ---------- Bausteine für die Fenster (unter Linux: bisheriges Aussehen) ----------

MARGIN, SPACING = 20, 12


def window(widget, layout=None) -> None:
    """Fenster als Fluent-Fenster markieren (Hintergrund, Mica, Ränder). Linux: unverändert."""
    if not active():
        return
    widget.setProperty("kidctlWindow", True)
    if layout is not None:
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(SPACING)


def title(text: str):
    """Überschrift im Fenster (Windows); ``None`` unter Linux – dort steht sie in der Titelleiste."""
    if not active():
        return None
    from .qt import QLabel
    label = QLabel(text)
    label.setProperty("role", "title")
    label.setWordWrap(True)
    return label


def secondary(label) -> None:
    if active():
        label.setProperty("role", "secondary")


def card(widget) -> None:
    if active():
        widget.setProperty("card", True)
        widget.setAttribute(_qt().WidgetAttribute.WA_StyledBackground, True)


def primary(button) -> None:
    """Hauptaktion: gefüllt in Akzentfarbe (Windows); Linux: Standardknopf wie bisher."""
    button.setProperty("primary", True)
    if hasattr(button, "setDefault"):
        button.setDefault(True)


def form(rows):
    """Eingabefelder: Windows Beschriftung über dem Feld (Fluent), Linux ``QFormLayout`` wie bisher.
    ``rows``: Liste von (Beschriftung oder None, Widget oder Layout)."""
    from .qt import QFormLayout, QLabel, QVBoxLayout
    if not active():
        lay = QFormLayout()
        for label, item in rows:
            if label is None:
                lay.addRow(item)
            else:
                lay.addRow(label, item)
        return lay
    lay = QVBoxLayout()
    lay.setSpacing(6)
    for label, item in rows:
        if label:
            text = QLabel(label.rstrip(":"))
            text.setProperty("role", "field")
            lay.addWidget(text)
        (lay.addLayout if hasattr(item, "addWidget") else lay.addWidget)(item)
    return lay


def _qt():
    from .qt import Qt
    return Qt


def round_corners(widget, small: bool = False) -> None:
    """Abgerundete Ecken über DWM (Windows 11; Windows 10 ignoriert das)."""
    if not active():
        return
    import ctypes
    try:
        pref = ctypes.c_int(3 if small else 2)                            # DWMWCP_ROUNDSMALL / DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 33, ctypes.byref(pref), 4)
    except (AttributeError, OSError):
        pass


GNOME_LIGHT_BAR = 51   # ab dieser GNOME-Version folgt die obere Leiste dem Farbschema (in der VM geprüft)


def gnome_major() -> int:
    """Hauptversion von GNOME Shell (0 = unbekannt), einmal ermittelt."""
    global _GNOME
    if _GNOME is None:
        import re
        import subprocess
        try:
            out = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True, timeout=5).stdout
            _GNOME = int(m.group(1)) if (m := re.search(r"(\d+)\.", out)) else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            _GNOME = 0
    return _GNOME


_GNOME: int | None = None


def taskbar_light() -> bool:
    """Leiste hell – dann dunkles Tray-Symbol. Windows: ``SystemUsesLightTheme``. Linux: Plasma und GNOME ab
    Version 51 folgen dem Farbschema (Qt ≥ 6.5, sonst Helligkeit der Palette); ältere GNOME haben eine dunkle Leiste."""
    if sys.platform != "win32":
        import os
        if "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() and gnome_major() < GNOME_LIGHT_BAR:
            return False
        try:
            from .qt import QPalette, Qt
            app = _app()
            scheme = app.styleHints().colorScheme()
            if scheme != Qt.ColorScheme.Unknown:
                return scheme == Qt.ColorScheme.Light
            return app.palette().color(QPalette.ColorRole.Window).lightness() >= 128
        except (AttributeError, RuntimeError):
            return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return int(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0]) == 1
    except (ImportError, OSError, ValueError):
        return False


def tray_icon(level: str | None, light_taskbar: bool, size: int = 64):
    """Monochromes KidCTL-Symbol (Stoppuhr) passend zur Taskleiste mit kleinem Statuspunkt (``level=None``: ohne)."""
    from .qt import QColor, QIcon, QPainter, QPen, QPixmap, QRectF, Qt
    fg = QColor("#1b1b1b" if light_taskbar else "#ffffff")
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64
    pen = QPen(fg, 5.5 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(10 * s, 14 * s, 42 * s, 42 * s))                 # Zifferblatt
    p.drawLine(int(31 * s), int(35 * s), int(31 * s), int(23 * s))        # Zeiger
    p.drawLine(int(31 * s), int(35 * s), int(39 * s), int(39 * s))
    p.drawLine(int(25 * s), int(6 * s), int(37 * s), int(6 * s))          # Krone
    p.drawLine(int(31 * s), int(6 * s), int(31 * s), int(13 * s))
    if level is None:
        p.end()
        return QIcon(pix)
    # Statuspunkt unten rechts mit ausgespartem Rand
    dot = QRectF(40 * s, 40 * s, 22 * s, 22 * s)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("black"))
    p.drawEllipse(dot.adjusted(-3 * s, -3 * s, 3 * s, 3 * s))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.setBrush(QColor(status_color(level, not light_taskbar)))
    p.drawEllipse(dot)
    p.end()
    return QIcon(pix)


def logo_png(path: str, size: int = 96) -> str | None:
    """App-Logo für Toasts als PNG (Toasts nehmen kein ICO): KidCTL-Symbol in Akzent-Blau auf Weiß."""
    try:
        from .qt import QColor, QPainter, QPixmap, QRectF, Qt
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(KIDCTL_BLUE))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.end()
        icon = tray_icon(None, False, size)   # weißes Symbol ohne Statuspunkt
        p = QPainter(pix)
        icon.paint(p, int(size * 0.12), int(size * 0.12), int(size * 0.76), int(size * 0.76))
        p.end()
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path if pix.save(path, "PNG") else None
    except Exception:   # Toast dann eben ohne Logo
        log.debug("Toast-Logo nicht erzeugt", exc_info=True)
        return None


def _install_window_hook(app) -> None:
    from .qt import QEvent, QObject, QWidget

    class Hook(QObject):
        def eventFilter(self, obj, event):   # noqa: N802 (Qt)
            # auch QWindow-Objekte kommen hier vorbei – nur Widgets (Fenster) behandeln
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow() \
                    and obj.property("kidctlWindow"):
                enable_mica(obj, STYLE.dark)
            return False
    app._kidctl_hook = Hook(app)   # Referenz halten
    app.installEventFilter(app._kidctl_hook)


def apply(app, platform: str | None = None) -> bool:
    """Windows-Stil einschalten (nur Windows). True = aktiv."""
    if not active(platform):
        return False
    from .qt import QFont
    font = QFont(FONT)
    if not font.exactMatch():
        font = QFont(FONT_FALLBACK)
    font.setPointSize(10)
    app.setFont(font)
    STYLE.refresh(app)
    _install_window_hook(app)
    try:   # Windows-Schema/Akzent live umschalten (Qt ≥ 6.5)
        app.styleHints().colorSchemeChanged.connect(lambda *_: STYLE.refresh(app))
    except AttributeError:
        pass
    return True


def enable_mica(widget, dark: bool) -> bool:
    """Mica (Windows 11) und dunkle Titelleiste passend zum Schema. Mica wirkt auf den Fensterrahmen; die Fläche
    malt Qt in der Fluent-Grundfarbe (``window``), die Mica im Ruhezustand entspricht – ein durchsichtiger
    Fensterinhalt ist mit Qt-Widgets unter Windows fehleranfällig. True = Mica aktiv."""
    if sys.platform != "win32":
        return False
    import ctypes
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)       # DWMWA_USE_IMMERSIVE_DARK_MODE
        if not mica_available():
            return False
        backdrop = ctypes.c_int(2)                                         # DWMSBT_MAINWINDOW (Mica)
        corner = ctypes.c_int(2)                                           # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
        return dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4) == 0   # DWMWA_SYSTEMBACKDROP_TYPE
    except (AttributeError, OSError):
        log.debug("Mica nicht verfügbar", exc_info=True)
        return False
