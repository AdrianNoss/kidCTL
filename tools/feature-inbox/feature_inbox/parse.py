"""Parser für Mails im Format ``KIDCTL-FEATURE-REQUEST v1`` (erzeugt vom Formular „Feature vorschlagen“).

Aufbau des Textteils::

    KIDCTL-FEATURE-REQUEST v1
    ID: <uuid>
    Datum: <ISO-8601>
    Kategorie: <kategorie>
    Wichtigkeit: <niedrig|mittel|hoch|->
    KidCTL-Version: <version>
    Kontakt: <mail oder ->
    ---
    Titel: <titel>
    ---
    <beschreibung>

Alles, was nicht exakt passt, ist ``None`` (→ Ordner „Unsortiert“). Inhalte gelten als nicht vertrauenswürdig;
der Parser prüft nur die Form und begrenzt Längen.
"""

from __future__ import annotations

import email
import re
import uuid
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage

MAGIC = "KIDCTL-FEATURE-REQUEST v1"
MAX_TITLE = 120
MAX_DESCRIPTION = 5000
IMPORTANCE = {"niedrig", "mittel", "hoch", "-"}
_HEADER_KEYS = ("ID", "Datum", "Kategorie", "Wichtigkeit", "KidCTL-Version", "Kontakt")
_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
# Steuerzeichen außer Zeilenumbruch und Tab entfernen (auch bidirektionale Steuerzeichen)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


@dataclass(frozen=True)
class FeatureRequest:
    request_id: str
    date: str
    category: str
    importance: str  # niedrig|mittel|hoch|-
    version: str
    contact: str | None  # nur intern speichern, nie ins Issue
    title: str
    description: str


def clean(text: str, limit: int) -> str:
    text = _CTRL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return text.strip()[:limit]


def body_text(raw: bytes) -> str | None:
    """Textteil (text/plain) einer Mail; HTML-only-Mails gelten nicht als v1."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    if not isinstance(msg, EmailMessage):
        return None
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        return None
    try:
        return part.get_content()
    except (LookupError, UnicodeError, AssertionError):
        return None


def parse(raw: bytes) -> FeatureRequest | None:
    text = body_text(raw)
    if text is None:
        return None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # führende Leerzeilen tolerieren
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].strip() != MAGIC:
        return None
    if len(lines) < 1 + len(_HEADER_KEYS) + 4:
        return None
    fields: dict[str, str] = {}
    for key, line in zip(_HEADER_KEYS, lines[1 : 1 + len(_HEADER_KEYS)]):
        prefix = key + ":"
        if not line.startswith(prefix):
            return None
        fields[key] = line[len(prefix) :].strip()
    i = 1 + len(_HEADER_KEYS)
    if lines[i].strip() != "---" or not lines[i + 1].startswith("Titel:") or lines[i + 2].strip() != "---":
        return None
    title = clean(lines[i + 1][len("Titel:") :], MAX_TITLE)
    description = clean("\n".join(lines[i + 3 :]), MAX_DESCRIPTION)

    try:
        request_id = str(uuid.UUID(fields["ID"]))
    except ValueError:
        return None
    importance = fields["Wichtigkeit"].lower()
    if importance not in IMPORTANCE:
        importance = "-"
    contact = fields["Kontakt"]
    contact = contact if contact != "-" and _EMAIL_RE.match(contact) else None
    if not title or not description:
        return None
    return FeatureRequest(
        request_id=request_id,
        date=clean(fields["Datum"], 40),
        category=clean(fields["Kategorie"], 60),
        importance=importance,
        version=clean(fields["KidCTL-Version"], 40),
        contact=contact,
        title=title,
        description=description,
    )


def mask_email(addr: str | None) -> str:
    """Für Logs: ``a***@example.org``."""
    if not addr or "@" not in addr:
        return "-"
    local, _, domain = addr.partition("@")
    return f"{local[:1]}***@{domain}"
