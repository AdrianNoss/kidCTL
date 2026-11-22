#!/usr/bin/env python3
"""Design-Durchsicht: fotografiert alle Seiten des Eltern- und Verwaltungsbereichs (fiktive Demo-Daten).

Eigene Datenbank „kidctl_review“ im Container kidctl-pg von dev/devserver.sh (Entwicklungsdatenbank bleibt
unberührt), Temp-Datenverzeichnis, eigener Server im SaaS-Modus (damit auch Tickets, Rechnungen und /admin
erscheinen), Eltern-Account per CLI + Demo-Daten mit dev/demo_seed.py, Betreiber-Account per CLI.
Je Seite ganzseitig: mobil 390×844 und Desktop 1280×800, hell und dunkel.

Ausgabe: dev/design-review/<label>/<lang>/<light|dark>/<name>-<mobile|desktop>.png (nicht im Repo).
Zusätzlich meldet das Skript CSP-Verstöße und JS-Fehler aus der Browser-Konsole sowie waagerechtes Scrollen.

    dev/design-review.py --label vorher [--lang de] [--only dashboard,admin] [--port 8001]

Braucht im Dev-venv (~/.local/share/kidctl-dev/venv): playwright (+ chromium), pyotp, httpx.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pyotp
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dev"))
import demo_seed  # noqa: E402

PG = os.environ.get("KIDCTL_PG_CONTAINER", "kidctl-pg")   # wie tests/podman_pg.sh
DB_NAME = "kidctl_review"
DATABASE_URL = f"postgresql+psycopg://kidctl:kidctl-dev@127.0.0.1:55432/{DB_NAME}"
SIZES = {"mobile": (390, 844), "desktop": (1280, 800)}
OPERATOR, OP_PASSWORD = "betrieb", "betrieb-passwort-lang"
EMAIL = {"de": "mama@example.org", "en": "mom@example.org"}

# Name -> Pfad; {lena}/{max}/{pc}/{family}/{ticket}/{invoice} werden ersetzt
ANON = {
    "login": "/login",
    "signup": "/signup",
    "reset": "/reset",
    "admin-login": "/admin/login",
    "login-code": "/login",            # Schritt 2: Passwort im Browser absenden (LOGIN_STEP)
    "admin-login-code": "/admin/login",
}
PARENT = {
    "dashboard": "/",
    "children": "/children",
    "child-today": "/children/{max}",
    "child-times": "/children/{lena}/zeiten",
    "child-programs": "/children/{lena}/programme",
    "child-internet": "/children/{lena}/internet",
    "child-blocked": "/children/{lena}/protokoll",
    "stats": "/children/{lena}/stats",
    "chat": "/children/{max}/chat",
    "devices": "/devices",
    "device": "/devices/{pc}",
    "parents": "/parents",
    "profile": "/profile",
    "settings": "/settings",
    "tickets": "/support",
    "ticket": "/support/{ticket}",
}
ADMIN = {
    "admin": "/admin",
    "admin-family": "/admin/families/{family}",
    "admin-tickets": "/admin/tickets",
    "admin-ticket": "/admin/tickets/{ticket}",
    "admin-invoices": "/admin/invoices",
    "admin-invoice": "/admin/invoices/{invoice}",
    "admin-invoice-settings": "/admin/invoice-settings",
    "admin-codes": "/admin/codes",
    "admin-log": "/admin/log",
    "admin-more": "/admin/more",
    "admin-branding": "/admin/branding",
    "admin-mail-preview": "/admin/mail-preview",
    "admin-newsletters": "/admin/newsletter",
    "admin-profile": "/admin/profile",
}
ALL = {**ANON, **PARENT, **ADMIN}
# Seiten hinter Schritt 1 der Anmeldung: Name -> (Benutzer-Feld, Passwort) je Sprache
LOGIN_STEP = {"login-code": (lambda lang: EMAIL[lang], demo_seed.PASSWORD),
              "admin-login-code": (lambda lang: OPERATOR, OP_PASSWORD)}


def psql(*args: str) -> None:
    subprocess.run(["podman", "exec", PG, *args], check=True, capture_output=True)


def fresh_database() -> None:
    if subprocess.run(["podman", "container", "exists", PG]).returncode != 0:
        raise SystemExit(f"Container {PG} fehlt – einmal dev/devserver.sh setup ausführen.")
    subprocess.run(["podman", "start", PG], check=True, capture_output=True)
    for _ in range(30):
        if subprocess.run(["podman", "exec", PG, "pg_isready", "-U", "kidctl"], capture_output=True).returncode == 0:
            break
        time.sleep(1)
    drop_database()
    psql("createdb", "-U", "kidctl", DB_NAME)


def drop_database() -> None:
    psql("dropdb", "-U", "kidctl", "--if-exists", "--force", DB_NAME)


def server_env(data: Path, port: int) -> dict:
    return {**os.environ, "KIDCTL_DEV": "1", "SECRET_KEY": "design-review", "KIDCTL_DATA_DIR": str(data),
            "DATABASE_URL": DATABASE_URL, "KIDCTL_MODE": "saas", "BASE_URL": f"http://localhost:{port}",
            "COOKIE_SECURE": "0", "PYTHONPATH": f"{REPO / 'server'}:{REPO / 'common'}"}


def cli(env: dict, *args: str, **extra: str) -> str:
    return subprocess.run([sys.executable, "-m", "app.cli", *args], cwd=REPO / "server", check=True,
                          env={**env, **extra}, capture_output=True, text=True).stdout


def start_server(env: dict, base: str, port: int, data: Path) -> subprocess.Popen:
    log = open(data / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=REPO / "server", env=env, stdout=log, stderr=log)
    for _ in range(100):
        try:
            httpx.get(base + "/login", timeout=1)
            return proc
        except httpx.HTTPError:
            if proc.poll() is not None:
                raise SystemExit(f"Server beendet, siehe {data / 'server.log'}")
            time.sleep(0.2)
    proc.terminate()
    raise SystemExit("Server startet nicht")


def csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


async def admin_login(base: str, secret: str, lang: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=base, headers={"Accept-Language": lang})
    await c.post("/admin/login", data={"csrf_token": csrf((await c.get("/admin/login")).text),
                                       "username": OPERATOR, "password": OP_PASSWORD})
    await c.post("/admin/login/code", data={"csrf_token": csrf((await c.get("/admin/login/code")).text),
                                            "totp": pyotp.TOTP(secret).now()})
    if (await c.get("/admin")).status_code != 200:
        raise SystemExit("Betreiber-Anmeldung fehlgeschlagen")
    return c


async def extra_data(web, op: httpx.AsyncClient) -> dict:
    """Ticket (Eltern), eigene Programme mit langen Pfaden und Rechnungsentwurf (Verwaltung), damit die Detailseiten
    auch schwierige Inhalte zeigen."""
    ids: dict = {}
    for path, name in ((r"C:\Program Files\WindowsApps\Microsoft.GamingServices_39.118.30001.0_x64__8wekyb3d8bbwe"
                        r"\GamingServicesUI\gamingservicesui.exe", ""),
                       (r"C:\ProgramData\Battle.net\Agent\Agent.9886\Agent.exe", "Agent.exe"),
                       ("/usr/bin/git", "Git")):
        await web.post(f"/children/{web.ids['Lena']}/programs/custom", {"path": path, "name": name})
    r = await web.c.post("/support", data={"csrf_token": await web.csrf("/support"),
                                           "subject": "Gerät lässt sich nicht koppeln", "category": "tech",
                                           "body": "Der Kopplungscode wird nicht angenommen."})
    m = re.search(r"/support/(\d+)", r.headers.get("location", "") + (await web.c.get("/support")).text)
    ids["ticket"] = m.group(1) if m else "1"
    html = (await op.get("/admin")).text
    m = re.search(r"/admin/families/(\d+)", html)
    ids["family"] = m.group(1) if m else "1"
    await op.post(f"/admin/families/{ids['family']}/invoices",
                  data={"csrf_token": csrf((await op.get(f"/admin/families/{ids['family']}")).text)})
    m = re.search(r"/admin/invoices/(\d+)", (await op.get("/admin/invoices")).text)
    ids["invoice"] = m.group(1) if m else "1"
    return ids


async def shoot(lang: str, label: str, port: int, only: set[str], keep: bool) -> int:
    base = f"http://localhost:{port}"   # WebAuthn akzeptiert keine IP als RP-ID
    out = REPO / "dev/design-review" / label / lang
    data = Path(tempfile.mkdtemp(prefix=f"kidctl-review-{lang}-"))
    fresh_database()
    env = server_env(data, port)
    secret = re.search(r"Secret: (\w+)", cli(env, "create-parent", KIDCTL_BOOTSTRAP_USER=demo_seed.PARENT[lang],
                                             KIDCTL_BOOTSTRAP_EMAIL=EMAIL[lang],
                                             KIDCTL_BOOTSTRAP_PASSWORD=demo_seed.PASSWORD)).group(1)
    op_secret = re.search(r"Secret: (\w+)", cli(env, "create-operator", KIDCTL_OPERATOR_USER=OPERATOR,
                                                KIDCTL_OPERATOR_PASSWORD=OP_PASSWORD)).group(1)
    proc = start_server(env, base, port, data)
    agents, problems = [], 0
    try:
        web, agents = await demo_seed.seed(base, DATABASE_URL, secret, lang)
        op = await admin_login(base, op_secret, lang)
        ids = {"lena": web.ids["Lena"], "max": web.ids["Max"], "pc": agents[1].device_id,
               **await extra_data(web, op)}
        jars = {"parent": [{"name": c.name, "value": c.value, "url": base} for c in web.c.cookies.jar],
                "admin": [{"name": c.name, "value": c.value, "url": base + "/admin"} for c in op.cookies.jar],
                "anon": []}
        await web.c.aclose()
        await op.aclose()
        groups = [("anon", ANON), ("parent", PARENT), ("admin", ADMIN)]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            for theme in ("light", "dark"):
                for size, (w, h) in SIZES.items():
                    for who, pages in groups:
                        ctx = await browser.new_context(
                            viewport={"width": w, "height": h}, color_scheme=theme,
                            locale="de-DE" if lang == "de" else "en-US", timezone_id="Europe/Berlin",
                            is_mobile=size == "mobile", has_touch=size == "mobile")
                        await ctx.add_cookies(jars[who])
                        page = await ctx.new_page()
                        msgs: list[str] = []
                        # Passkey-Vorschlag im Login: Headless-Chromium hat keinen Authenticator (erwartet, nur Konsole)
                        page.on("console", lambda m: m.type in ("error", "warning")
                                and not m.text.startswith("Passkey:") and msgs.append(m.text))
                        page.on("pageerror", lambda e: msgs.append(str(e)))
                        for name, path in pages.items():
                            if only and name not in only:
                                continue
                            msgs.clear()
                            resp = await page.goto(base + path.format(**ids), wait_until="networkidle")
                            if name in LOGIN_STEP:
                                user, password = LOGIN_STEP[name]
                                await page.fill("input[name=username]", user(lang))
                                await page.fill("input[name=password]", password)
                                await page.click("button[type=submit]")
                                await page.wait_for_url("**/login/code", wait_until="networkidle")
                            if "/login" in page.url and who != "anon":
                                raise SystemExit(f"{name}: nicht angemeldet")
                            await page.wait_for_timeout(200)
                            target = out / theme / f"{name}-{size}.png"
                            target.parent.mkdir(parents=True, exist_ok=True)
                            await page.screenshot(path=str(target), full_page=True)
                            overflow = await page.evaluate(
                                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                            notes = [f"HTTP {resp.status}"] if resp and resp.status >= 400 else []
                            if overflow > 0:
                                notes.append(f"waagerecht {overflow}px zu breit")
                            notes += [f"Konsole: {m[:160]}" for m in msgs]
                            if notes:
                                problems += 1
                                print(f"!! {lang}/{theme}/{name}-{size}: " + " | ".join(notes))
                        await ctx.close()
            await browser.close()
        print(f"Bilder: {out}")
    finally:
        for a in agents:
            await a.close()
        proc.terminate()
        proc.wait()
        if keep:
            print(f"Daten behalten: {data}, Datenbank {DB_NAME}")
        else:
            shutil.rmtree(data, ignore_errors=True)
            drop_database()
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="nachher", help="Unterordner, z. B. vorher/nachher")
    ap.add_argument("--lang", default="de")
    ap.add_argument("--only", default="", help="nur diese Seiten (Komma-Liste): " + ",".join(ALL))
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--keep", action="store_true", help="Datenverzeichnis und Datenbank behalten (Fehlersuche)")
    a = ap.parse_args()
    only = {x for x in a.only.split(",") if x}
    if unknown := only - ALL.keys():
        raise SystemExit(f"Unbekannte Seite(n): {', '.join(sorted(unknown))}")
    problems = sum(asyncio.run(shoot(lang, a.label, a.port, only, a.keep)) for lang in a.lang.split(","))
    print(f"{problems} Auffälligkeit(en) aus Konsole/Layout" if problems else "Keine Auffälligkeiten aus Konsole/Layout")


if __name__ == "__main__":
    main()
