"""/.well-known/security.txt (RFC 9116) – ohne Anmeldung. Kontakt des Projekts, optional zuerst der des Betreibers
(Umgebungsvariable SECURITY_CONTACT: E-Mail-Adresse oder https-URL)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ..config import settings

router = APIRouter()

PROJECT_CONTACTS = ("mailto:security@nossco.de", "https://github.com/AdrianNoss/kidCTL/security/advisories/new")
POLICY = "https://adriannoss.github.io/kidCTL/docs/security/"


def _contact(value: str) -> str:
    value = value.strip()
    if value.lower().startswith(("mailto:", "https://", "tel:")):
        return value
    return f"mailto:{value}" if "@" in value else ""


def security_txt(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    contacts = [c for c in (_contact(os.environ.get("SECURITY_CONTACT", "")),) if c] + list(PROJECT_CONTACTS)
    expires = (now + timedelta(days=365)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"Contact: {c}" for c in dict.fromkeys(contacts)]
    lines += [f"Expires: {expires}", f"Policy: {POLICY}", "Preferred-Languages: en, de",
              f"Canonical: {settings.base_url}/.well-known/security.txt"]
    return "\n".join(lines) + "\n"


@router.get("/.well-known/security.txt", include_in_schema=False)
def well_known_security_txt():
    return PlainTextResponse(security_txt(), headers={"Cache-Control": "public, max-age=86400"})
