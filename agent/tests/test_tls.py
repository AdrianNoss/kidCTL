"""TLS-Kontext: unter Windows prüft ``truststore``, unter Linux bleibt der Standard der Bibliotheken."""

from __future__ import annotations

import asyncio
import ssl
import sys
import types
import urllib.request
from pathlib import Path

from kidctl_agent import connection, tls

LOCK = Path(__file__).resolve().parents[2] / "packaging" / "windows" / "vendor.lock"


def _fake_truststore(monkeypatch):
    fake = types.SimpleNamespace(SSLContext=lambda protocol: ssl.SSLContext(protocol))
    monkeypatch.setitem(sys.modules, "truststore", fake)
    monkeypatch.setattr(tls.sys, "platform", "win32")


def test_linux_keeps_library_default():
    assert tls.context() is None and tls.handlers() == []


def test_windows_uses_truststore_with_verification(monkeypatch):
    _fake_truststore(monkeypatch)
    ctx = tls.context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert isinstance(tls.handlers()[0], urllib.request.HTTPSHandler)


def test_urlopen_passes_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(tls, "context", lambda: "ctx")
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout, context: seen.update(url=url, ctx=context))
    tls.urlopen("https://x/", timeout=5)
    assert seen == {"url": "https://x/", "ctx": "ctx"}


def _ssl_argument(monkeypatch, device):
    seen = {}

    def fake_connect(url, **kwargs):
        seen.update(kwargs)
        raise OSError("Ende des Tests")

    monkeypatch.setattr(connection, "connect", fake_connect)
    monkeypatch.setattr(tls, "context", lambda: "ctx")
    try:
        asyncio.run(connection.Connection(device, None)._session())
    except OSError:
        pass
    return seen["ssl"]


def test_connection_sets_context_only_for_wss(monkeypatch):
    assert _ssl_argument(monkeypatch, {"server_url": "https://k.example", "token": "t"}) == "ctx"
    assert _ssl_argument(monkeypatch, {"server_url": "http://k.example", "token": "t", "insecure_http": True}) is None


def test_windows_package_ships_truststore():
    assert any(line.startswith("wheel truststore ") for line in LOCK.read_text().splitlines())
