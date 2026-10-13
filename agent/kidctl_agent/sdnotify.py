"""Minimales sd_notify (ohne Abhängigkeit): meldet systemd die Bereitschaft (Type=notify)."""

from __future__ import annotations

import os
import socket


def notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstrakter Socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
        s.connect(addr)
        s.sendall(state.encode())
    return True
