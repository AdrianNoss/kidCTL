"""Jinja2-Templates und Render-Helfer."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings
from .security import CSRF_COOKIE, CSRF_FIELD, set_cookie

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_TZ = ZoneInfo(settings.timezone)


def localtime(value: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if value is None:
        return "–"
    return value.replace(tzinfo=timezone.utc).astimezone(_TZ).strftime(fmt)


templates.env.filters["localtime"] = localtime
templates.env.globals["CSRF_FIELD"] = CSRF_FIELD


def render(request: Request, template: str, status_code: int = 200, **ctx):
    sess = getattr(request.state, "session", None)
    ctx.setdefault("current_parent", sess.parent if sess else None)
    ctx.setdefault("csrf_token", sess.csrf_token if sess else "")
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def render_anon(request: Request, name: str, status_code: int = 200, **ctx):
    """Seiten ohne Login: CSRF per Double-Submit-Cookie."""
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    resp = templates.TemplateResponse(
        request, name, {**ctx, "csrf_token": token, "current_parent": None}, status_code=status_code)
    set_cookie(resp, CSRF_COOKIE, token, max_age=3600, samesite="strict")
    return resp
