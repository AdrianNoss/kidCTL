"""Sprache des Servers: Katalog laden, Sprache je Anfrage bestimmen, Sprache eines Elternteils für Mails/Push.

Vorrang im WebUI: im Profil fest gewählte Sprache > Umschalter auf der Anmeldeseite (Cookie) > Browser
(``Accept-Language``). Mails und Push: feste Sprache, sonst die zuletzt erkannte, sonst Deutsch.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from sqlalchemy import select

from kidctl_common.i18n import (  # noqa: F401 – Weitergabe an die Module des Servers
    LANGS, N_, _, fmt_date, fmt_datetime, fmt_minutes, fmt_percent, from_accept_language, gettext, month_name,
    ngettext, normalize, set_current, use, weekday, weekday_names,
)
from kidctl_common.i18n import current, install

from .db import SessionLocal
from .models import Parent, ParentSession
from .security import SESSION_COOKIE, token_hash

LANG_COOKIE = "kidctl_lang"
LANGUAGE_CHOICES = {"": N_("Automatisch (Browser)"), "de": "Deutsch", "en": "English"}

install(Path(__file__).parent / "locale" / "en.po")


def browser_language(request: Request) -> str:
    cookie = request.cookies.get(LANG_COOKIE, "")
    if cookie in LANGS:
        return cookie
    return from_accept_language(request.headers.get("accept-language"))


def request_language(request: Request) -> str:
    """Sprache dieser Anfrage (mit Blick auf die Profileinstellung eines angemeldeten Elternteils)."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with SessionLocal() as db:
            lang = db.scalar(select(Parent.language).join(ParentSession, ParentSession.parent_id == Parent.id)
                             .where(ParentSession.token_hash == token_hash(token)))
        if lang in LANGS:
            return lang
    return browser_language(request)


def parent_language(parent: Parent) -> str:
    """Sprache für Mails und Push an ``parent``."""
    for lang in (parent.language, parent.detected_language):
        if lang in LANGS:
            return lang
    return "de"


__all__ = ["LANG_COOKIE", "LANGUAGE_CHOICES", "SESSION_COOKIE", "browser_language", "current", "parent_language",
           "request_language"]
