from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import alarms, blocklog, i18n, policy, report, webfilter
from .config import settings
from .db import SessionLocal, init_db
from .routes import admin, api, auth, pwa
from .security import LoginRequired

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    tasks = [asyncio.create_task(alarms.run_forever()), asyncio.create_task(_refresh_lists_forever()),
             asyncio.create_task(_prune_log_forever()), asyncio.create_task(report.run_forever())]
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
    """Protokoll gesperrter Aktionen: Einträge nach 90 Tagen löschen (beim Start, dann alle 6 Stunden)."""
    log = logging.getLogger("kidctl.blocklog")
    while True:
        try:
            def run() -> int:
                with SessionLocal() as db:
                    return blocklog.prune(db)
            if n := await asyncio.to_thread(run):
                log.info("Protokoll: %d Einträge älter als %d Tage gelöscht", n, blocklog.RETENTION_DAYS)
        except Exception:
            log.exception("Protokoll nicht aufräumbar")
        await asyncio.sleep(blocklog.PRUNE_INTERVAL)


app = FastAPI(title="KidCTL", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

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


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(api.router)
app.include_router(pwa.router)
app.include_router(admin.router)
