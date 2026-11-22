"""Jinja2-Templates und Render-Helfer."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import blocklog, i18n, integrity, versions
from .i18n import _, ngettext
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


def asset(name: str) -> str:
    """URL einer statischen Datei mit Prüfsumme, damit Browser nach einem Update nicht die alte Fassung nehmen."""
    if name not in _assets:
        digest = hashlib.sha256((Path(__file__).parent / "static" / name).read_bytes()).hexdigest()[:10]
        _assets[name] = f"/static/{name}?v={digest}"
    return _assets[name]


_assets: dict[str, str] = {}


def fmt_relative(value: datetime | None) -> str:
    """„vor 5 Min.“, „vor 3 Std.“, „vor 2 Tagen“; älter als 60 Tage: Datum. UTC aus der DB."""
    if value is None:
        return "–"
    if value.tzinfo is not None:                     # z. B. Backup-Zeitpunkt mit Zeitzone
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - value).total_seconds()
    if seconds < 60:
        return _("gerade eben")
    if seconds < 3600:
        n = int(seconds // 60)
        return ngettext("vor {n} Min.", "vor {n} Min.", n).format(n=n)
    if seconds < 86400:
        n = int(seconds // 3600)
        return ngettext("vor {n} Std.", "vor {n} Std.", n).format(n=n)
    n = int(seconds // 86400)
    if n <= 60:
        return ngettext("vor {n} Tag", "vor {n} Tagen", n).format(n=n)
    return i18n.fmt_date(value.date())


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
                             today_minus=_today_minus, asset=asset, fmt_relative=fmt_relative)
from . import branding  # noqa: E402
def admin_nav(operator) -> list[tuple[str, str, str, bool]]:
    """Bereiche des Verwaltungsbereichs je Rolle: (Pfad, Text, Icon, in der unteren Leiste am Handy). Am Handy drei
    Einträge + „Mehr“ (/admin/more mit allen übrigen); ab 840 px alle in der Leiste links."""
    _ = i18n.gettext
    items = [("/admin", _("Familien"), "families", True), ("/admin/tickets", _("Tickets"), "support", True)]
    if operator.role == "admin":
        items.append(("/admin/invoices", _("Rechnungen"), "invoice", True))
        items.append(("/admin/codes", _("Einladungscodes"), "key", False))
        if settings.saas:
            items.append(("/admin/newsletter", _("Newsletter"), "newsletter", False))
        items += [("/admin/mail-preview", _("Mails"), "mail", False),
                  ("/admin/branding", _("Erscheinungsbild"), "palette", False)]
        items.append(("/admin/log", _("Protokoll"), "log", False))
    else:
        items.append(("/admin/log", _("Protokoll"), "log", True))
    items.append(("/admin/info", _("Info"), "info", False))
    # Lange Wörter in der schmalen Leiste mit weichem Trennstrich umbrechen (Browser trennen Deutsch nicht immer)
    soft = {"Einladungscodes": "Einladungs\u00adcodes", "Erscheinungsbild": "Erscheinungs\u00adbild"}
    return [(href, soft.get(label, label), ic, mobile) for href, label, ic, mobile in items]


def pct(value: float, total: float) -> int:
    """Anteil in ganzen Prozent (0–100) für die Klassen ``p-0``…``p-100`` in style.css – Diagramme ohne Inline-Style,
    die CSP verbietet ihn."""
    return max(0, min(100, round(100 * value / total))) if total else 0


templates.env.globals["pct"] = pct
templates.env.globals["admin_nav"] = admin_nav
templates.env.globals["brand"] = branding.current          # Whitelabel: Name, Logo, Icon (kurz zwischengespeichert)
templates.env.filters["brand"] = branding.apply            # „KidCTL“ in Texten an Familien → eigener Name
templates.env.globals["SOURCE_URL"] = settings.source_url
from . import about  # noqa: E402
templates.env.globals["about_info"] = about.info              # Info-Abschnitt: Version, Links, neuere Version
templates.env.globals["unseen_notes"] = about.unseen_notes    # Hinweis „Neu in Version …“ auf der Übersicht
templates.env.globals["REGISTRATION"] = settings.registration   # open | invite | closed
templates.env.globals["SAAS"] = settings.saas            # Betriebsart: viele Familien, Anmeldung nur per E-Mail


def is_local_path(url: str) -> bool:
    """Nur Pfade auf diesem Server: beginnt mit „/“, nicht „//“ oder „/\\“ (Browser lesen das als fremde Adresse),
    ohne Schema/Host und ohne Steuerzeichen."""
    from urllib.parse import urlsplit
    if not url.startswith("/") or url[1:2] in ("/", "\\") or any(ord(ch) < 32 for ch in url) or "\\" in url:
        return False
    parts = urlsplit(url)
    return not parts.scheme and not parts.netloc


def local_redirect(url: str, fallback: str = "/", status_code: int = 303):
    """Weiterleitung nur auf lokale Pfade (sonst ``fallback``) – Schutz vor offenen Weiterleitungen."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url if is_local_path(url) else fallback, status_code=status_code)


def render(request: Request, template: str, status_code: int = 200, **ctx):
    sess = getattr(request.state, "session", None)
    ctx.setdefault("current_parent", sess.parent if sess else None)
    ctx.setdefault("csrf_token", sess.csrf_token if sess else "")
    ctx.setdefault("support_view", getattr(request.state, "support", None))
    ctx.setdefault("billing_banner", getattr(request.state, "billing", None))
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def render_anon(request: Request, name: str, status_code: int = 200, **ctx):
    """Seiten ohne Login: CSRF per Double-Submit-Cookie."""
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    resp = templates.TemplateResponse(
        request, name, {**ctx, "csrf_token": token, "current_parent": None}, status_code=status_code)
    set_cookie(resp, CSRF_COOKIE, token, max_age=3600, samesite="strict")
    return resp
