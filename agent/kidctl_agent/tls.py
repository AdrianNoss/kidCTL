"""TLS für alle Verbindungen zum Server.

Windows hält nur wenige Stammzertifikate lokal vor und lädt fehlende erst bei einer Prüfung über die Windows-API
nach. Python liest aber nur den lokalen Speicher, auf frischen Rechnern scheitert deshalb die Prüfung
(``CERTIFICATE_VERIFY_FAILED``). ``truststore`` lässt Windows selbst prüfen. Linux nutzt unverändert die System-CAs.
"""

from __future__ import annotations

import sys
import urllib.request


def context():
    """SSL-Kontext für den Server; ``None`` heißt Standard der Bibliothek (Linux)."""
    if sys.platform != "win32":
        return None
    import ssl

    import truststore
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def urlopen(url, timeout: float):
    return urllib.request.urlopen(url, timeout=timeout, context=context())


def handlers() -> list:
    """Zusätzliche Handler für ``urllib.request.build_opener``."""
    ctx = context()
    return [urllib.request.HTTPSHandler(context=ctx)] if ctx else []
