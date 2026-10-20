"""Freigabe-Anfragen: Das Kind fragt ein gesperrtes Programm bzw. eine Website an; Eltern erlauben
dauerhaft, nur heute oder lehnen ab.

- Dauerhaft: Programm aus dem Katalog -> Freigabe in der Programmliste; sonst als eigenes Programm
  (Name aus der Anfrage). Website -> erlaubte Domain (und ggf. aus der eigenen Sperrliste entfernt).
- Nur heute: ``ChildTempGrant`` für den heutigen lokalen Tag; der Agent lässt die Freigabe um
  Mitternacht auch offline ablaufen.
- Je Kind und Schlüssel höchstens eine offene Anfrage (weitere werden der offenen zugeordnet).
"""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from . import programs
from . import schedule as sch
from .chat import _existing, child_for_user
from .models import ChildDomain, ChildTempGrant, DeviceApp, Message, utcnow
from .webfilter import normalize_domain

SCOPES = {"permanent": "Dauerhaft erlaubt", "today": "Heute erlaubt"}
_FLATPAK = re.compile(r"^flatpak:[A-Za-z0-9_.-]{3,255}$")


class AccessError(ValueError):
    pass


def valid_key(kind: str, key: str) -> str | None:
    """Geprüfter Schlüssel (Websites normalisiert) oder ``None``."""
    if kind == "domain":
        d = normalize_domain(key.removeprefix("domain:")) if key.startswith("domain:") else None
        return f"domain:{d}" if d else None
    if key.startswith("flatpak:"):
        return key if _FLATPAK.match(key) else None
    try:
        return programs.custom_key(key[4:]) if key.startswith("exe:/") else None
    except ValueError:
        return None


def receive(db: Session, device_id: int, msg: p.AccessRequest) -> tuple[p.Ack, Message | None]:
    """Anfrage speichern. (Ack, neue Message oder None bei Duplikat/Fehler)."""
    dup = _existing(db, device_id, msg.client_msg_id)
    if dup is not None:
        return p.Ack(client_msg_id=msg.client_msg_id, ok=True, message_id=dup.id), None
    child = child_for_user(db, device_id, msg.username)
    if child is None:
        return p.Ack(client_msg_id=msg.client_msg_id, ok=False, error="user_not_mapped"), None
    key = valid_key(msg.access_kind, msg.key)
    if key is None:
        return p.Ack(client_msg_id=msg.client_msg_id, ok=False, error="invalid_key"), None
    open_ = db.scalar(select(Message).where(Message.child_id == child.id, Message.kind == "access_request",
                                            Message.status == "pending", Message.access_key == key))
    if open_ is not None:
        return p.Ack(client_msg_id=msg.client_msg_id, ok=True, message_id=open_.id), None
    m = Message(child_id=child.id, from_child=True, device_id=device_id, client_msg_id=msg.client_msg_id,
                kind="access_request", body=msg.reason.strip(), status="pending", request_day=sch.today_local(),
                access_kind=msg.access_kind, access_key=key, access_name=msg.name.strip()[:255] or key)
    db.add(m)
    db.commit()
    return p.Ack(client_msg_id=msg.client_msg_id, ok=True, message_id=m.id), m


def decide(db: Session, msg: Message, parent_id: int, scope: str | None) -> None:
    """``scope`` = permanent | today, ``None`` = ablehnen. Atomar gegen gleichzeitige Entscheidungen."""
    if msg.kind != "access_request":
        raise AccessError("Keine Freigabe-Anfrage.")
    if scope is not None and scope not in SCOPES:
        raise AccessError("Unbekannte Entscheidung.")
    res = db.execute(update(Message).where(Message.id == msg.id, Message.status == "pending")
                     .values(status="approved" if scope else "rejected", access_scope=scope,
                             decided_by=parent_id, decided_at=utcnow()))
    if res.rowcount != 1:
        db.rollback()
        raise AccessError("Die Anfrage wurde bereits entschieden.")
    if scope == "permanent":
        _allow_permanently(db, msg)
    elif scope == "today":
        today = sch.today_local()
        db.execute(delete(ChildTempGrant).where(ChildTempGrant.day < today - timedelta(days=1)))
        exists = db.scalar(select(ChildTempGrant).where(ChildTempGrant.child_id == msg.child_id,
                                                        ChildTempGrant.key == msg.access_key,
                                                        ChildTempGrant.day == today))
        if exists is None:
            db.add(ChildTempGrant(child_id=msg.child_id, key=msg.access_key, day=today))
    db.commit()
    db.refresh(msg)


def _allow_permanently(db: Session, msg: Message) -> None:
    key, child_id = msg.access_key, msg.child_id
    if msg.access_kind == "domain":
        domain = key.removeprefix("domain:")
        db.execute(delete(ChildDomain).where(ChildDomain.child_id == child_id, ChildDomain.domain == domain,
                                             ChildDomain.action == "deny"))
        if db.scalar(select(ChildDomain).where(ChildDomain.child_id == child_id, ChildDomain.domain == domain,
                                               ChildDomain.action == "allow")) is None:
            db.add(ChildDomain(child_id=child_id, domain=domain, action="allow"))
        return
    in_catalog = db.scalar(select(DeviceApp.id).where(DeviceApp.key == key).limit(1)) is not None
    if key.startswith("exe:") and not in_catalog:
        programs.add_custom(db, child_id, key[4:], msg.access_name or "")   # erscheint unter „Eigene Programme“
    else:
        programs.set_allowed(db, child_id, set(programs.allowed_keys(db, child_id)) | {key})


def temp_grants(db: Session, child_id: int) -> list[p.TempGrant]:
    since = sch.today_local() - timedelta(days=1)   # Puffer für Uhr-/Zeitzonenränder
    return [p.TempGrant(key=g.key, day=g.day) for g in db.scalars(
        select(ChildTempGrant).where(ChildTempGrant.child_id == child_id, ChildTempGrant.day >= since)
        .order_by(ChildTempGrant.day, ChildTempGrant.key))]
