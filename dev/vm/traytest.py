#!/usr/bin/env python3
"""Tray-Socket aus Sicht eines (unprivilegierten) Users ansprechen – für VM-Tests des Agenten.

Läuft mit dem System-Python ohne Zusatzpakete, z. B. in der VM als Kind:

    sudo -u kind python3 /opt/kidctl-src/dev/vm/traytest.py state
    sudo -u kind python3 /opt/kidctl-src/dev/vm/traytest.py stall 60     # Anfragen senden, nie lesen
    sudo -u kind python3 /opt/kidctl-src/dev/vm/traytest.py flood 50     # 50 Chat-Nachrichten schnell hintereinander

state: hello senden, ersten Zustand ausgeben. stall: hello + dauerhaft get_blocked, ohne je zu lesen, bis der
Agent trennt oder die Zeit abläuft. flood: N Chat-Nachrichten, danach die Antworten zusammengefasst.
"""

import json
import socket
import sys
import time
from collections import Counter

SOCK = "/run/kidctl/tray.sock"


def connect() -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(json.dumps({"type": "hello", "version": 1, "tray_icon": True}).encode() + b"\n")
    return s


def read_msgs(s: socket.socket, seconds: float) -> list[dict]:
    s.settimeout(seconds)
    buf, out = b"", []
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            out += [json.loads(x) for x in lines if x]
    except socket.timeout:
        pass
    return out


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "state"
    s = connect()
    if mode == "state":
        st = read_msgs(s, 2)[0]
        print({k: st.get(k) for k in ("restricted", "allowed", "remaining_seconds", "used_seconds", "reason",
                                      "online", "pending_outbox")})
    elif mode == "stall":
        limit = float(sys.argv[2]) if len(sys.argv) > 2 else 60
        s.setblocking(False)
        start, sent = time.monotonic(), 0
        req = json.dumps({"type": "get_blocked"}).encode() + b"\n"
        while time.monotonic() - start < limit:
            try:
                s.send(req)
                sent += 1
            except BlockingIOError:
                time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                print(f"getrennt nach {time.monotonic() - start:.1f} s, {sent} Anfragen gesendet")
                return
        print(f"nicht getrennt nach {limit:.0f} s, {sent} Anfragen gesendet")
    elif mode == "flood":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        for i in range(n):
            s.sendall(json.dumps({"type": "chat_send", "body": f"Flut {i}"}).encode() + b"\n")
        msgs = read_msgs(s, 5)
        print(Counter(m["type"] + (":" + m.get("message", "")[:60] if m["type"] == "error" else "") for m in msgs))
        print("pending_outbox:", [m.get("pending_outbox") for m in msgs if m["type"] == "state"][-1:])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
