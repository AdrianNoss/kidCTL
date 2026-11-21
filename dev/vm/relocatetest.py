#!/usr/bin/env python3
"""Server-Umzug (``relocate``) gegen den installierten Agenten prüfen – ohne den laufenden Dienst anzufassen.

In der VM als root, mit dem Agenten aus dem Paket und einem eigenen Zustandsordner:

    sudo python3 -I /opt/kidctl-src/dev/vm/relocatetest.py

Ein über https gekoppeltes Gerät bekommt eine ``relocate``-Nachricht wie vom Server (über den Protokoll-Parser)
auf ``http://``- bzw. ein unbekanntes ``https://``-Ziel und muss beim alten Server bleiben. Geprüft werden
beide Schichten: der Protokoll-Parser und ``Connection._relocate``.
"""

import asyncio
import json
import logging
import os
import sys
import tempfile

os.environ["KIDCTL_STATE_DIR"] = tempfile.mkdtemp(prefix="kidctl-relocate-")
sys.path.insert(0, "/usr/lib/kidctl")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from kidctl_agent import store                      # noqa: E402
from kidctl_agent.connection import Connection      # noqa: E402
from kidctl_common import protocol as p             # noqa: E402
from pydantic import ValidationError                # noqa: E402

OLD = "https://alt.example.invalid"


async def check(target: str) -> bool:
    device = {"server_url": OLD, "device_id": 1, "token": "x"}
    store.save(store.DEVICE_FILE, device)
    conn = Connection(dict(device), handler=None)
    try:   # wie Connection._run: ungültige Nachrichten werden verworfen
        p.server_message_adapter.validate_json(json.dumps({"type": "relocate", "server_url": target}))
        print(f"  Parser nimmt {target} an")
    except ValidationError:
        print(f"  Parser verwirft {target}")
    try:
        await conn._relocate(target)   # zweite Schicht direkt prüfen
    except Exception as e:   # noqa: BLE001 – Ausnahme hier würde die Serververbindung beenden
        print(f"FEHLER relocate {target}: {type(e).__name__}: {e}")
        return False
    saved = store.load(store.DEVICE_FILE)
    ok = saved["server_url"] == OLD and conn.url.startswith("wss://alt.")
    print(f"{'OK ' if ok else 'FEHLER'} relocate {target}: gespeichert {saved['server_url']}, Verbindung {conn.url}")
    return ok


async def main() -> int:
    results = [await check("http://10.0.2.2:8000"), await check("https://neu.example.invalid")]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
