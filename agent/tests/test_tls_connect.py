"""Verbindung zu https-Servern: websockets (ab 14) lehnt ``ssl=None`` bei ``wss://`` mit ValueError ab – der Agent
darf dann gar kein ``ssl`` übergeben (Linux) bzw. nur seinen eigenen Kontext (Windows)."""

import asyncio
import sys

import pytest
from websockets.asyncio.client import connect

from kidctl_agent import connection, tls


def _attempt(url: str) -> BaseException:
    async def go():
        try:
            async with connect(url, **connection._tls(url), open_timeout=2):
                pass
        except BaseException as e:   # noqa: BLE001 – Art des Fehlers ist das Prüfziel
            return e
    return asyncio.run(go())


@pytest.mark.parametrize("url", ["wss://127.0.0.1:9/api/v1/ws", "ws://127.0.0.1:9/api/v1/ws"])
def test_connect_arguments_accepted(url):
    """Nur ein Netzwerkfehler (Port zu), kein Fehler wegen unpassender TLS-Argumente."""
    assert isinstance(_attempt(url), OSError)


def test_windows_context_only_for_wss(monkeypatch):
    monkeypatch.setattr(tls, "context", lambda: "ctx")
    assert connection._tls("wss://a/x") == {"ssl": "ctx"} and connection._tls("ws://a/x") == {}
    monkeypatch.setattr(tls, "context", lambda: None)
    assert connection._tls("wss://a/x") == {}


def test_linux_context_is_default():
    if sys.platform != "win32":
        assert tls.context() is None and connection._tls("wss://a/x") == {}
