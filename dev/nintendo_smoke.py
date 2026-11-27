#!/usr/bin/env python3
"""Manueller Smoke-Test der Nintendo-Anbindung mit echtem Konto (nicht in CI).

Ruft einmal ab und zeigt, was kidCTL daraus macht (app.nintendo.snapshot). Schreibt nichts in eine Datenbank.

  # Session-Token vorhanden:
  NINTENDO_SESSION_TOKEN=… PYTHONPATH=server:common KIDCTL_DEV=1 python dev/nintendo_smoke.py

  # Ohne Token: Login-Link anzeigen, kopierten npf…-Link einfügen; --show-token gibt das neue Token einmal aus
  PYTHONPATH=server:common KIDCTL_DEV=1 python dev/nintendo_smoke.py --show-token

Das Session-Token ist ein Vollzugriff auf das Nintendo-Konto: nicht in Dateien, Tickets oder Chats kopieren.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import aiohttp
from pynintendoparental import Authenticator, NintendoParental

from app import nintendo


async def main(show_token: bool) -> int:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=nintendo.TIMEOUT)) as session:
        token = os.environ.get("NINTENDO_SESSION_TOKEN", "").strip()
        if token:
            auth = Authenticator(session_token=token, client_session=session)
            await auth.async_complete_login(use_session_token=True)
        else:
            auth = Authenticator(client_session=session)
            print("1. Link öffnen (Gerät ohne Nintendo-Parental-Controls-App):\n\n" + auth.login_url + "\n")
            print("2. Rechtsklick auf „Diese Person auswählen“ → Link-Adresse kopieren, hier einfügen:")
            await auth.async_complete_login(input("> ").strip())
            if show_token:
                print("\nSession-Token (geheim halten):", auth.session_token)
        api = NintendoParental(auth, nintendo.settings.timezone, nintendo.API_LANG)
        devices = await api.discover_devices()
        for dev in devices.values():
            try:
                await dev.update(monthly_summary=False)
            except Exception as e:   # wie im Server: eine Konsole darf die anderen nicht aufhalten
                print(f"! Konsole {dev.device_id}: {type(e).__name__}: {e}")
            c = nintendo.snapshot(dev)
            print(f"\nKonsole {c.name!r} ({c.model}), id={c.key}")
            print(f"  heute {c.used_minutes} min, Limit {c.limit_minutes}, Restzeit {c.remaining_minutes}")
            for p in c.players:
                print(f"  Profil {p.nickname!r} ({p.key}): {p.minutes} min – {p.apps}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--show-token", action="store_true", help="nach dem Login das Session-Token ausgeben")
    sys.exit(asyncio.run(main(ap.parse_args().show_token)))
