"""Qt-Bindung: PySide6, sonst PyQt6 (Ubuntu 24.04 und Systeme darauf haben kein PySide6).

Die Tray-App importiert Qt nur von hier. ``KIDCTL_QT=pyqt6`` erzwingt PyQt6 (Tests).
"""

from __future__ import annotations

import os

if os.environ.get("KIDCTL_QT") == "pyqt6":
    _order = ("PyQt6", "PySide6")
else:
    _order = ("PySide6", "PyQt6")

API = None
for _name in _order:
    try:
        if _name == "PySide6":
            from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets
            Signal = QtCore.Signal
        else:
            from PyQt6 import QtCore, QtGui, QtNetwork, QtWidgets
            Signal = QtCore.pyqtSignal
        API = _name
        break
    except ImportError:
        continue
if API is None:
    raise ImportError("weder PySide6 noch PyQt6 installiert")

QObject, QTimer, QEvent, QRect, Qt, QTime, QElapsedTimer, QCoreApplication = (
    QtCore.QObject, QtCore.QTimer, QtCore.QEvent, QtCore.QRect, QtCore.Qt, QtCore.QTime, QtCore.QElapsedTimer,
    QtCore.QCoreApplication)
QAction, QColor, QFont, QIcon, QPainter, QPalette, QPixmap = (
    QtGui.QAction, QtGui.QColor, QtGui.QFont, QtGui.QIcon, QtGui.QPainter, QtGui.QPalette, QtGui.QPixmap)
QLocalServer, QLocalSocket = QtNetwork.QLocalServer, QtNetwork.QLocalSocket
_W = ("QApplication", "QButtonGroup", "QCheckBox", "QDialog", "QDialogButtonBox", "QFormLayout", "QHBoxLayout",
      "QLabel", "QLineEdit", "QMenu", "QMessageBox", "QPlainTextEdit", "QPushButton", "QRadioButton", "QScrollArea",
      "QSpinBox", "QSystemTrayIcon", "QTextBrowser", "QTimeEdit", "QVBoxLayout", "QWidget")
globals().update({n: getattr(QtWidgets, n) for n in _W})
