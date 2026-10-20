"""Chat Eltern <-> Kind und Verlängerungsanfragen."""

from __future__ import annotations

import logging
from datetime import time

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from . import push
from . import schedule as sch
from .models import Child, Device, DeviceUser, Message, Parent, utcnow
from .ws import manager

log = logging.getLogger("kidctl.chat")


def sender_name(db: Session, msg: Message, child: Child | None = None) -> str:
    if msg.from_child:
        return (child or db.get(Child, msg.child_id)).name
    parent = db.get(Parent, msg.sender_parent_id) if msg.sender_parent_id else None
    return (parent.display_name or parent.username) if parent else "Eltern"


def to_wire(db: Session, msg: Message) -> p.ChatMessage:
    return p.ChatMessage(
        id=msg.id, child_id=msg.child_id, from_child=msg.from_child,
        sender_name=sender_name(db, msg), kind=msg.kind, body=msg.body, created_at=msg.created_at,
        request_day=msg.request_day, requested_minutes=msg.requested_minutes,
        requested_window_end=msg.requested_window_end, status=msg.status,
        approved_minutes=msg.approved_minutes, approved_window_end=msg.approved_window_end,
        access_kind=msg.access_kind, access_key=msg.access_key, access_name=msg.access_name,
        access_scope=msg.access_scope,
    )


def child_for_user(db: Session, device_id: int, username: str) -> Child | None:
    du = db.scalar(select(DeviceUser).where(
        DeviceUser.device_id == device_id, DeviceUser.username == username))
    return du.child if du and du.child_id else None


def device_ids_for_child(db: Session, child_id: int) -> set[int]:
    return set(db.scalars(
        select(DeviceUser.device_id).join(Device)
        .where(DeviceUser.child_id == child_id, Device.revoked_at.is_(None))))


async def push_to_child(db: Session, msg: Message) -> None:
    """Stellt einen (neuen oder geänderten) Eintrag allen online-Geräten des Kindes zu.

    Offline-Geräte holen ihn beim nächsten ``chat_sync`` nach.
    """
    payload = to_wire(db, msg).model_dump(mode="json")
    for device_id in device_ids_for_child(db, msg.child_id):
        try:
            await manager.send(device_id, payload)
        except Exception:
            log.warning("Zustellung an Gerät %s fehlgeschlagen", device_id, exc_info=True)


def expire_stale_requests(db: Session) -> None:
    """Offene Anfragen für vergangene Tage verfallen (Tagesausnahmen gelten nur für ihr Datum)."""
    db.execute(update(Message)
               .where(Message.kind == "extension_request", Message.status == "pending",
                      Message.request_day < sch.today_local())
               .values(status="expired"))
    db.commit()


def pending_count(db: Session) -> dict[int, int]:
    """Offene Anfragen je Kind (Verlängerungen und Freigaben; für Übersicht/Navigation)."""
    out = dict(db.execute(
        select(Message.child_id, func.count()).where(
            Message.kind == "extension_request", Message.status == "pending",
            Message.request_day >= sch.today_local())
        .group_by(Message.child_id)).all())
    for child_id, n in db.execute(select(Message.child_id, func.count()).where(
            Message.kind == "access_request", Message.status == "pending").group_by(Message.child_id)):
        out[child_id] = out.get(child_id, 0) + n
    return out


def notify_parents_new(db: Session, msg: Message) -> None:
    """Web Push an alle Eltern für eine neue Nachricht/Anfrage des Kindes."""
    name = sender_name(db, msg)
    if msg.kind == "extension_request":
        parts = []
        if msg.requested_minutes:
            parts.append(f"+{msg.requested_minutes} min")
        if msg.requested_window_end:
            parts.append(f"bis {msg.requested_window_end:%H:%M} Uhr")
        title, body = f"{name} bittet um Verlängerung ({', '.join(parts)})", msg.body
    elif msg.kind == "access_request":
        what = "die Website" if msg.access_kind == "domain" else "das Programm"
        title = f"{name} bittet um Freigabe: {msg.access_name}"
        body = msg.body or f"Anfrage für {what} {msg.access_name}"
    else:
        title, body = f"Nachricht von {name}", msg.body
    push.notify_parents(title, body, url=f"/children/{msg.child_id}/chat#m{msg.id}",
                        tag=f"chat-{msg.child_id}")


# ---------- Eingang vom Agenten ----------

def _existing(db: Session, device_id: int, client_msg_id: str) -> Message | None:
    return db.scalar(select(Message).where(
        Message.device_id == device_id, Message.client_msg_id == client_msg_id))


def receive_from_agent(db: Session, device_id: int,
                       msg: p.ChatSend | p.ExtensionRequest) -> tuple[p.Ack, Message | None]:
    """Speichert eine Kind-Nachricht. Gibt (Ack, neue Message oder None bei Duplikat/Fehler) zurück."""
    dup = _existing(db, device_id, msg.client_msg_id)
    if dup is not None:
        return p.Ack(client_msg_id=msg.client_msg_id, ok=True, message_id=dup.id), None
    child = child_for_user(db, device_id, msg.username)
    if child is None:
        return p.Ack(client_msg_id=msg.client_msg_id, ok=False, error="user_not_mapped"), None

    m = Message(child_id=child.id, from_child=True, device_id=device_id,
                client_msg_id=msg.client_msg_id)
    if isinstance(msg, p.ChatSend):
        m.kind, m.body = "text", msg.body.strip()
    else:
        if not msg.minutes and msg.window_end is None:
            return p.Ack(client_msg_id=msg.client_msg_id, ok=False, error="empty_request"), None
        m.kind, m.body = "extension_request", msg.reason.strip()
        m.request_day, m.requested_minutes = msg.day, msg.minutes
        m.requested_window_end = msg.window_end
        m.status = "pending" if msg.day >= sch.today_local() else "expired"
    db.add(m)
    db.commit()
    return p.Ack(client_msg_id=msg.client_msg_id, ok=True, message_id=m.id), m


def history_for_agent(db: Session, device_id: int, sync: p.ChatSync, limit: int = 200) -> list[Message]:
    child = child_for_user(db, device_id, sync.username)
    if child is None:
        return []
    rows = db.scalars(select(Message).where(Message.child_id == child.id, Message.id > sync.after_id)
                      .order_by(Message.id.desc()).limit(limit)).all()
    return list(reversed(rows))


# ---------- Entscheidungen der Eltern ----------

def decide(db: Session, msg: Message, parent_id: int, approve: bool,
           minutes: int = 0, window_end: time | None = None) -> None:
    """Genehmigt (ggf. angepasst) oder lehnt ab. Wirft ScheduleError; Commit erfolgt hier.

    Atomar gegen gleichzeitige Entscheidungen zweier Eltern: Status wird nur von ``pending``
    aus geändert.
    """
    if msg.kind != "extension_request":
        raise sch.ScheduleError("Keine Verlängerungsanfrage.")
    child = db.get(Child, msg.child_id)
    if approve:
        sch.check_credit(child, msg.request_day, minutes, window_end)
    elif msg.request_day < sch.today_local():
        raise sch.ScheduleError("Anfrage ist bereits verfallen.")

    res = db.execute(update(Message)
                     .where(Message.id == msg.id, Message.status == "pending")
                     .values(status="approved" if approve else "rejected", decided_by=parent_id,
                             decided_at=utcnow(),
                             approved_minutes=minutes if approve else None,
                             approved_window_end=window_end if approve else None))
    if res.rowcount != 1:
        db.rollback()
        raise sch.ScheduleError("Die Anfrage wurde bereits entschieden.")
    if approve:
        sch.add_credit(db, child, msg.request_day, minutes, window_end)
    db.commit()
    db.refresh(msg)
