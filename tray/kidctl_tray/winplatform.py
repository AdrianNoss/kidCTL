"""Windows-Anbindung der Tray-App: Sprache, AppUserModelID, Toasts mit Knopf, Protokoll ``kidctl://``.

Toasts aus einer Anwendung ohne Store-Paket brauchen eine Startmenü-Verknüpfung mit derselben AppUserModelID
(``APP_ID``; legt der Installer an). Knöpfe öffnen ``kidctl://action/<id>``: Windows startet dafür die Tray-App
mit der Adresse als Argument, die neue Instanz reicht sie an die laufende weiter (siehe ``app.main``), und die
meldet den Klick an den Agenten (``TrayAction``). Ohne die WinRT-Pakete zeigt die Tray-App einfache
Meldungen über das Tray-Symbol (ohne Knopf; ein Klick auf die Meldung löst die Aktion aus).
"""

from __future__ import annotations

import ctypes
import getpass
import logging
import os
import re
from xml.sax.saxutils import escape, quoteattr

log = logging.getLogger("kidctl.tray")

APP_ID = "KidCTL.Tray"
SCHEME = "kidctl"
ACTION_URL = re.compile(r"^kidctl://action/([0-9a-f]{1,64})/?$")
SHOW_URL = re.compile(r"^kidctl://(show|open)?/?$")


def user_language() -> str:
    """``de`` bei deutscher Anzeigesprache des Kontos, sonst ``en``."""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return "de"
    return "de" if langid & 0x3FF == 0x07 else "en"


def set_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID))
    except (AttributeError, OSError):
        pass


def instance_name() -> str:
    """Name der Einzelinstanz-Pipe: je Sitzung und Benutzer (Pipe-Namen gelten rechnerweit)."""
    session = ctypes.c_ulong()
    try:
        ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    except (AttributeError, OSError):
        pass
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())
    return f"kidctl-tray-{session.value}-{user}"


def parse_url(arg: str) -> tuple[str, str] | None:
    """``kidctl://action/<id>`` -> ("action", id); ``kidctl://`` -> ("show", "")."""
    if m := ACTION_URL.match(arg):
        return "action", m.group(1)
    if SHOW_URL.match(arg):
        return "show", ""
    return None


def toast_xml(title: str, body: str, action_id: str | None, action_label: str, critical: bool) -> str:
    actions = ""
    scenario = ""
    if action_id and action_label:
        actions = (f'<actions><action content={quoteattr(action_label)} activationType="protocol" '
                   f'arguments={quoteattr(f"{SCHEME}://action/{action_id}")}/></actions>')
        if critical:
            scenario = ' scenario="reminder"'   # bleibt stehen, bis das Kind reagiert
    return (f'<toast activationType="protocol" launch="{SCHEME}://show"{scenario}><visual>'
            f'<binding template="ToastGeneric"><text>{escape(title)}</text><text>{escape(body)}</text>'
            f'</binding></visual>{actions}</toast>')


class Toaster:
    """Windows-Toasts über WinRT (Pakete ``winrt-Windows.UI.Notifications`` und ``winrt-Windows.Data.Xml.Dom``)."""

    def __init__(self) -> None:
        self.notifier = None
        try:
            from winrt.windows.ui.notifications import ToastNotificationManager

            try:
                self.notifier = ToastNotificationManager.create_toast_notifier_with_id(APP_ID)
            except AttributeError:
                self.notifier = ToastNotificationManager.create_toast_notifier(APP_ID)
        except Exception:
            log.warning("Windows-Toasts nicht verfügbar – einfache Meldungen über das Tray-Symbol", exc_info=True)

    @property
    def available(self) -> bool:
        return self.notifier is not None

    def show(self, title: str, body: str, action_id: str | None, action_label: str, critical: bool) -> bool:
        if self.notifier is None:
            return False
        try:
            from winrt.windows.data.xml.dom import XmlDocument
            from winrt.windows.ui.notifications import ToastNotification

            doc = XmlDocument()
            doc.load_xml(toast_xml(title, body, action_id, action_label, critical))
            self.notifier.show(ToastNotification(doc))
            return True
        except Exception:
            log.exception("Toast nicht anzeigbar")
            return False
