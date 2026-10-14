"""Kommandozeile: kidctl enroll | run | status."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import urllib.error
import urllib.request

from kidctl_common import protocol as p

from . import store

log = logging.getLogger("kidctl")


def _require_root() -> None:
    # Tests setzen KIDCTL_STATE_DIR und laufen ohne root
    if os.geteuid() != 0 and "KIDCTL_STATE_DIR" not in os.environ:
        sys.exit("Muss als root laufen (sudo).")


def enroll(server_url: str, code: str, force: bool = False, insecure_http: bool = False) -> int:
    _require_root()
    server_url = server_url.rstrip("/")
    # Ohne TLS gingen Kopplungscode und Geräte-Token im Klartext übers Netz -> nur zum Testen
    if not (server_url.startswith("https://") or (insecure_http and server_url.startswith("http://"))):
        sys.exit("Server-URL muss mit https:// beginnen (http:// nur zum Testen mit --insecure-http).")
    if store.load(store.DEVICE_FILE) and not force:
        sys.exit("Gerät ist bereits gekoppelt. Neu koppeln mit --force.")

    body = p.EnrollRequest(code=code, hostname=socket.gethostname()).model_dump_json().encode()
    req = urllib.request.Request(server_url + p.ENROLL_PATH, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            res = p.EnrollResponse.model_validate_json(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        sys.exit(f"Kopplung fehlgeschlagen ({e.code}): {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Server nicht erreichbar: {e.reason}")

    store.save(store.DEVICE_FILE, {"server_url": server_url, "device_id": res.device_id,
                                   "token": res.token, "insecure_http": insecure_http})
    print(f"Gekoppelt als Gerät {res.device_id}. Agent (neu) starten: systemctl restart kidctl-agent")
    return 0


def run() -> int:
    _require_root()
    from .agent import Agent
    from .logind import Logind
    from .usersession import UserSession

    async def main() -> None:
        agent = Agent(Logind(), UserSession(), store.load(store.DEVICE_FILE))
        task = asyncio.create_task(agent.run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            log.info("Agent beendet")

    asyncio.run(main())
    return 0


def status() -> int:
    from .agent import Agent

    device = store.load(store.DEVICE_FILE)
    print(f"Server:  {device['server_url']} (Gerät {device['device_id']})" if device else "Server:  nicht gekoppelt")
    agent = Agent(logind=None, desktop=None)
    if agent.policy is None:
        print("Policy:  keine – niemand wird eingeschränkt")
        return 0
    pol = agent.policy
    print(f"Policy:  {pol.version} ({pol.timezone})")
    now = agent.now_local()
    names = {c.id: c.name for c in pol.children}
    for user, d in sorted(agent.decisions(now).items()):
        cid = pol.users[user]
        used = agent.usage.used_seconds(cid, [u for u, c in pol.users.items() if c == cid], now.date())
        state = f"erlaubt, noch {d.remaining_seconds // 60} min" if d.allowed else f"gesperrt ({d.reason})"
        print(f"  {user:<16} → {names.get(cid, cid)}: heute {used // 60} min genutzt, {state}")
    return 0


def main() -> int:
    logging.basicConfig(level=os.environ.get("KIDCTL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="kidctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="Gerät mit Kopplungscode beim Server registrieren")
    e.add_argument("server_url")
    e.add_argument("code")
    e.add_argument("--force", action="store_true", help="bestehende Kopplung ersetzen")
    e.add_argument("--insecure-http", action="store_true",
                   help="http:// erlauben (nur Test-Server, Token unverschlüsselt)")
    sub.add_parser("run", help="Agent starten (systemd)")
    sub.add_parser("status", help="Policy und heutige Nutzung anzeigen")
    args = ap.parse_args()
    if args.cmd == "enroll":
        return enroll(args.server_url, args.code, args.force, args.insecure_http)
    if args.cmd == "run":
        return run()
    return status()


if __name__ == "__main__":
    sys.exit(main())
