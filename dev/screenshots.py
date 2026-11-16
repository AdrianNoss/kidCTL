#!/usr/bin/env python3
"""Screenshots der Eltern-WebUI für die Projektseite (fiktive Demo-Daten, nie echte).

Je Sprache: frisches Datenverzeichnis, eigener Server auf 127.0.0.1:8010 (nicht ~/.local/share/kidctl-dev),
Eltern-Account per CLI, Demo-Daten mit dev/demo_seed.py (simulierte Agenten bleiben verbunden), Anmeldung mit TOTP,
dann Playwright: mobil 390×844 und Desktop 1280×800, hell und dunkel.
Ausgabe: website/static/img/screenshots/<lang>/<light|dark>/<name>-<mobile|desktop>.webp

    dev/screenshots.py [--lang de,en] [--only dashboard,stats] [--out DIR] [--keep]

Braucht im Dev-venv (~/.local/share/kidctl-dev/venv): playwright (+ `python -m playwright install chromium`), pillow.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dev"))
import demo_seed  # noqa: E402

PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"
SIZES = {"mobile": (390, 844), "desktop": (1280, 800)}
# Name -> Pfad ({lena}/{max} = Kind-ID). Abgestimmt mit Adrian 2026-11-16.
PAGES = {
    "dashboard": "/",
    "child-today": "/children/{max}",
    "child-times": "/children/{lena}/zeiten",
    "child-programs": "/children/{lena}/programme",
    "child-internet": "/children/{lena}/internet",
    "stats": "/children/{lena}/stats",
    "chat": "/children/{max}/chat",
    "blocked": "/children/{lena}/protokoll",
    "devices": "/devices",
}


def server_env(data: Path) -> dict:
    return {**os.environ, "KIDCTL_DEV": "1", "SECRET_KEY": "screenshot-demo", "KIDCTL_DATA_DIR": str(data),
            "BASE_URL": "https://kids.example.org", "COOKIE_SECURE": "0",
            "PYTHONPATH": f"{REPO / 'server'}:{REPO / 'common'}"}


def start_server(data: Path, lang: str) -> tuple[subprocess.Popen, str]:
    env = server_env(data)
    out = subprocess.run([sys.executable, "-m", "app.cli", "create-parent"], cwd=REPO / "server", check=True,
                         env={**env, "KIDCTL_BOOTSTRAP_USER": demo_seed.PARENT[lang],
                              "KIDCTL_BOOTSTRAP_PASSWORD": demo_seed.PASSWORD},
                         capture_output=True, text=True).stdout
    secret = re.search(r"Secret: (\w+)", out).group(1)
    log = open(data / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(PORT)], cwd=REPO / "server", env=env, stdout=log, stderr=log)
    for _ in range(100):
        try:
            httpx.get(BASE + "/login", timeout=1)
            return proc, secret
        except httpx.HTTPError:
            if proc.poll() is not None:
                raise SystemExit(f"Server beendet, siehe {data / 'server.log'}")
            time.sleep(0.2)
    proc.terminate()
    raise SystemExit("Server startet nicht")


async def shoot(lang: str, out: Path, only: set[str], keep: bool) -> None:
    data = Path(tempfile.mkdtemp(prefix=f"kidctl-shots-{lang}-"))
    proc, secret = start_server(data, lang)
    agents = []
    try:
        web, agents = await demo_seed.seed(BASE, data, secret, lang)
        cookies = [{"name": c.name, "value": c.value, "url": BASE} for c in web.c.cookies.jar]
        await web.c.aclose()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            for theme in ("light", "dark"):
                for size, (w, h) in SIZES.items():
                    ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2,
                                                    color_scheme=theme, locale="de-DE" if lang == "de" else "en-US",
                                                    timezone_id="Europe/Berlin", is_mobile=size == "mobile",
                                                    has_touch=size == "mobile")
                    await ctx.add_cookies(cookies)
                    page = await ctx.new_page()
                    for name, path in PAGES.items():
                        if only and name not in only:
                            continue
                        await page.goto(BASE + path.format(lena=web.ids["Lena"], max=web.ids["Max"]),
                                        wait_until="networkidle")
                        if "/login" in page.url:
                            raise SystemExit("Nicht angemeldet")
                        if name == "chat":  # Chat scrollt ans Ende
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(300)
                        png = await page.screenshot()
                        target = out / lang / theme / f"{name}-{size}.webp"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        img = Image.open(io.BytesIO(png))
                        img = img.resize((w * 3 // 2, h * 3 // 2), Image.LANCZOS)  # 1,5-fach: scharf, aber klein
                        img.save(target, "WEBP", quality=82, method=6)
                        print(f"{target}  {target.stat().st_size // 1024} KB")
                    await ctx.close()
            await browser.close()
    finally:
        for a in agents:
            await a.close()
        proc.terminate()
        proc.wait()
        if keep:
            print(f"Daten behalten: {data}")
        else:
            shutil.rmtree(data, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="de,en")
    ap.add_argument("--only", default="", help="nur diese Seiten (Komma-Liste): " + ",".join(PAGES))
    ap.add_argument("--out", type=Path, default=REPO / "website/static/img/screenshots")
    ap.add_argument("--keep", action="store_true", help="Datenverzeichnis nicht löschen (Fehlersuche)")
    a = ap.parse_args()
    only = {x for x in a.only.split(",") if x}
    if unknown := only - PAGES.keys():
        raise SystemExit(f"Unbekannte Seite(n): {', '.join(sorted(unknown))}")
    for lang in a.lang.split(","):
        asyncio.run(shoot(lang, a.out, only, a.keep))


if __name__ == "__main__":
    main()
