"""Verwaltungsprotokoll (Verwaltungsbereich 2, Etappe 1): wer hat wann was an einer Familie bzw. der Plattform geändert.

Schreiben immer in derselben Transaktion wie die Änderung (der Aufrufer committet). Akteur: Betreiber-Name,
``WEBHOOK`` oder ``SYSTEM`` (Hintergrundjobs).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from .models import AdminLog

WEBHOOK, SYSTEM = "Webhook", "System"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return str(value)


def actor_of(sess) -> tuple[str, int | None]:
    """(Name, ID) aus einer Betreiber-Sitzung."""
    return sess.operator.username, sess.operator_id


def record(db: Session, actor: str | tuple, obj: str, field: str = "", old=None, new=None, *,
           family_id: int | None = None, object_id: int | None = None) -> None:
    name, operator_id = actor if isinstance(actor, tuple) else (actor, None)
    db.add(AdminLog(actor=name[:64], operator_id=operator_id, family_id=family_id, object=obj, object_id=object_id,
                    field=field[:64], old=_text(old)[:2000], new=_text(new)[:2000]))


def snapshot(obj, fields) -> dict:
    return {f: getattr(obj, f) for f in fields}


def changes(db: Session, actor, obj: str, before: dict, after_obj, *, family_id=None, object_id=None) -> int:
    """Für jedes geänderte Feld einen Eintrag; Rückgabe: Anzahl."""
    n = 0
    for field, old in before.items():
        new = getattr(after_obj, field)
        if _text(old) != _text(new):
            record(db, actor, obj, field, old, new, family_id=family_id, object_id=object_id)
            n += 1
    return n
