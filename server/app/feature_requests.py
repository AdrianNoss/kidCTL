"""„Feature vorschlagen“: Eltern schicken einen Wunsch per Mail an ``FEATURE_REQUEST_EMAIL`` (Standard leer = aus).

Die Textfassung der Mail hat ein festes Format (``KIDCTL-FEATURE-REQUEST v1``), das der Postfach-Agent parst – nur nach
Rücksprache ändern. In der Datenbank bleibt kein Inhalt: Das Rate-Limit zählt Einträge in ``login_attempt``
(``kind="feature"``, gelöscht nach der Aufbewahrungsfrist der Anmeldeversuche).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import about, mailer
from .accounts import email_usable
from .config import settings
from .i18n import N_, _
from .models import LoginAttempt, Parent, utcnow

FORMAT = "KIDCTL-FEATURE-REQUEST v1"
CATEGORIES = {"platform": N_("Neue Plattform/Gerät"), "usability": N_("Bedienung"), "rules": N_("Regeln/Limits"),
              "notifications": N_("Benachrichtigungen"), "other": N_("Sonstiges")}
IMPORTANCE = {"low": N_("niedrig"), "medium": N_("mittel"), "high": N_("hoch")}
MAX_TITLE, MAX_DESCRIPTION = 120, 5000
DAILY_LIMIT = 5
KIND = "feature"

_TAGS = re.compile(r"<[a-zA-Z/!?][^>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")   # Steuerzeichen außer Tab und Zeilenumbruch


class FeatureRequestError(Exception):
    pass


@dataclass(frozen=True)
class FeatureRequest:
    id: str
    date: str
    category: str
    importance: str
    version: str
    contact: str
    title: str
    description: str


def clean(text: str, single_line: bool = False) -> str:
    """HTML-Tags und Steuerzeichen entfernen; Titel einzeilig."""
    text = _TAGS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _CONTROL.sub("", text)
    if single_line:
        text = " ".join(text.split())
    return text.strip()


def _key(parent: Parent) -> str:
    return f"parent:{parent.id}"


def used_today(db: Session, parent: Parent) -> int:
    return db.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.kind == KIND, LoginAttempt.username == _key(parent),
        LoginAttempt.at >= utcnow() - timedelta(days=1)))


def build(parent: Parent, title: str, description: str, category: str, importance: str,
          contact: bool) -> FeatureRequest:
    """Eingaben prüfen und den Vorschlag zusammenstellen (Kategorie/Wichtigkeit in der Mail immer deutsch)."""
    title, description = clean(title, single_line=True), clean(description)
    if not title or not description or category not in CATEGORIES:
        raise FeatureRequestError(_("Bitte Titel, Kategorie und Beschreibung angeben."))
    if len(title) > MAX_TITLE or len(description) > MAX_DESCRIPTION:
        raise FeatureRequestError(_("Der Titel darf höchstens {title} und die Beschreibung höchstens {text} Zeichen "
                                    "lang sein.").format(title=MAX_TITLE, text=MAX_DESCRIPTION))
    if importance and importance not in IMPORTANCE:
        raise FeatureRequestError(_("Ungültige Angabe zur Wichtigkeit."))
    return FeatureRequest(id=str(uuid.uuid4()), date=utcnow().replace(microsecond=0, tzinfo=timezone.utc).isoformat(),
                   category=CATEGORIES[category], importance=IMPORTANCE.get(importance, "-"), version=about.VERSION,
                   contact=parent.email if contact and email_usable(parent) else "-", title=title,
                   description=description)


def submit(db: Session, parent: Parent, **form) -> FeatureRequest:
    """Prüfen, Rate-Limit verbuchen, senden. Ein Fehlschlag beim Senden zählt nicht."""
    req = build(parent, **form)
    attempt = LoginAttempt(kind=KIND, ip="", username=_key(parent))   # erst verbuchen, dann zählen: auch parallel max. 5
    db.add(attempt)
    db.commit()
    if used_today(db, parent) > DAILY_LIMIT:
        db.delete(attempt)
        db.commit()
        raise FeatureRequestError(_("Du hast heute schon {n} Vorschläge geschickt. Bitte versuche es morgen wieder.")
                                  .format(n=DAILY_LIMIT))
    headers = {"Reply-To": req.contact} if req.contact != "-" else None
    if not mailer.send(settings.feature_request_email, "feature_request", "de", headers=headers, req=req):
        db.delete(attempt)
        db.commit()
        raise FeatureRequestError(_("Der Vorschlag konnte gerade nicht gesendet werden. Bitte versuche es später "
                                    "noch einmal."))
    return req


def sample() -> dict:
    """Beispieldaten für die Mail-Vorschau und Tests."""
    return dict(req=FeatureRequest(id="3f2c9a1e-7b4d-4c2a-9e51-0a6d8b2f4c11", date="2026-11-26T09:30:00+00:00",
                            category=CATEGORIES["platform"], importance=IMPORTANCE["high"], version=about.VERSION,
                            contact="mama@example.org", title="Nintendo Switch anbinden",
                            description="Unsere Kinder spielen viel auf der Switch.\n\nSchön wäre, wenn KidCTL die "
                                        "Spielzeit dort mitzählt."))
