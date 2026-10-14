from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import alarms, policy, webfilter
from .config import settings
from .db import init_db
from .routes import admin, api, auth, pwa
from .security import LoginRequired

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    tasks = [asyncio.create_task(alarms.run_forever()), asyncio.create_task(_refresh_lists_forever())]
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


app = FastAPI(title="kidctl", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

# Pacman-Repository (signiert; per kidctl-repo-publish.sh nach <data>/repo kopiert)
_repo_dir = Path(settings.data_dir) / "repo"
_repo_dir.mkdir(parents=True, exist_ok=True)
app.mount("/repo", StaticFiles(directory=str(_repo_dir)), name="repo")


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
