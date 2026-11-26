from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import about, alarms, backup, billing, tickets, i18n, nintendo, policy, report, versions, webfilter, retention, vacations
from .config import settings
from .db import init_db
from .tenant import platform_session
from .middleware import SecurityMiddleware
from .routes import admin, api, auth, billing as billing_routes, nintendo as nintendo_routes, operators, pwa, signup, wellknown
from .routes import tickets as ticket_routes
from .routes import (
    operator_branding, operator_codes, operator_families, operator_invoices, operator_log, operator_mails,
    operator_profile, operator_tickets,
)
from .security import FamilyPaused, LoginRequired
from .web import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    tasks = [asyncio.create_task(alarms.run_forever()), asyncio.create_task(_refresh_lists_forever()),
             asyncio.create_task(_prune_log_forever()), asyncio.create_task(report.run_forever()),
             asyncio.create_task(backup.run_forever()), asyncio.create_task(billing.run_forever()),
             asyncio.create_task(tickets.run_forever()), asyncio.create_task(about.run_forever()),
             asyncio.create_task(vacations.run_forever()), asyncio.create_task(nintendo.run_forever())]
    yield
    for t in tasks:
        t.cancel()


async def _refresh_lists_forever() -> None:
    """Sperrlisten täglich aktualisieren; geänderte an die betroffenen Geräte melden."""
    while True:
        try:
            changed = await webfilter.refresh_lists()
            if changed:
                await policy.push_for_lists(changed)
        except Exception:
            logging.getLogger("kidctl.webfilter").exception("Aktualisierung der Sperrlisten fehlgeschlagen")
        await asyncio.sleep(webfilter.REFRESH_SECONDS)


async def _prune_log_forever() -> None:
    """Aufbewahrungsfristen durchsetzen (app.retention): beim Start, dann alle 6 Stunden."""
    log = logging.getLogger("kidctl.retention")
    while True:
        try:
            def run() -> dict:
                with platform_session() as db:   # Aufräumen über alle Familien, nur nach Zeitstempeln
                    return retention.prune_all(db)
            await asyncio.to_thread(run)
        except Exception:
            log.exception("Aufräumen nach Aufbewahrungsfristen fehlgeschlagen")
        await asyncio.sleep(retention.PRUNE_INTERVAL)


app = FastAPI(title="KidCTL", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")



@app.get("/repo/", response_class=HTMLResponse)
@app.get("/repo/windows/", response_class=HTMLResponse)
def repo_listing(request: Request):
    """Download-Seite: zeigt nur die Windows-Installer, die Dateien selbst liefert der Mount darunter."""
    i18n.set_current(i18n.request_language(request))   # die Middleware lässt /repo/ aus
    return render(request, "repo.html", installers=versions.windows_installers())


# Pacman-Repository (signiert; per kidctl-repo-publish.sh nach <data>/repo kopiert)
_repo_dir = Path(settings.data_dir) / "repo"
_repo_dir.mkdir(parents=True, exist_ok=True)
app.mount("/repo", StaticFiles(directory=str(_repo_dir)), name="repo")


@app.middleware("http")
async def _language(request: Request, call_next):
    """Sprache der Anfrage für Texte in Code und Templates (ContextVar, gilt für den ganzen Ablauf)."""
    if not request.url.path.startswith(("/static/", "/repo/", "/api/")):
        i18n.set_current(await asyncio.to_thread(i18n.request_language, request))
    return await call_next(request)


@app.exception_handler(LoginRequired)
async def _login_required(_request: Request, _exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(FamilyPaused)
async def _family_paused(_request: Request, _exc: FamilyPaused):
    return RedirectResponse("/settings#abo", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(operators.router)
for _mod in (operator_families, operator_codes, operator_log, operator_profile, operator_tickets, operator_invoices,
             operator_branding, operator_mails):
    app.include_router(_mod.router)
app.include_router(signup.router)
app.include_router(billing_routes.router)
app.include_router(ticket_routes.router)
app.include_router(api.router)
app.include_router(pwa.router)
app.include_router(nintendo_routes.router)
app.include_router(wellknown.router)
app.include_router(admin.router)
