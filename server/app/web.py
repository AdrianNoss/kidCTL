"""Jinja2-Templates und Render-Helfer."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import blocklog, i18n, integrity, versions
from .config import settings
from .security import CSRF_COOKIE, CSRF_FIELD, set_cookie

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_TZ = ZoneInfo(settings.timezone)


def localtime(value: datetime | None, fmt: str | None = None) -> str:
    """UTC aus der DB -> Heimat-Zeitzone; ohne ``fmt`` Datum und Uhrzeit in der aktuellen Sprache."""
    if value is None:
        return "–"
    local = value.replace(tzinfo=timezone.utc).astimezone(_TZ)
    return local.strftime(fmt) if fmt else i18n.fmt_datetime(local)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", "," if i18n.current() == "de" else ".")
        n /= 1024
    return ""


def _today_minus(days: int):
    from datetime import timedelta
    return datetime.now(_TZ).date() - timedelta(days=days)


templates.env.filters["localtime"] = localtime
templates.env.globals["CSRF_FIELD"] = CSRF_FIELD
# Übersetzung und Formate (lesen die Sprache der laufenden Anfrage)
templates.env.globals.update(_=i18n.gettext, gettext=i18n.gettext, ngettext=i18n.ngettext, lang=i18n.current,
                             fmt_date=i18n.fmt_date, fmt_minutes=i18n.fmt_minutes, fmt_percent=i18n.fmt_percent,
                             weekday=i18n.weekday, LANGUAGE_CHOICES=i18n.LANGUAGE_CHOICES,
                             integrity_label=integrity.label_for, blocked_detail=blocklog.detail,
                             update_for=versions.update_for, fmt_datetime=i18n.fmt_datetime, fmt_size=_fmt_size,
                             today_minus=_today_minus)


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
