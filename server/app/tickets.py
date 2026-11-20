"""Support-Tickets (Verwaltungsbereich 2, Etappe 4; nur SaaS).

Status: ``new`` (Familie legt an) → ``waiting`` (Support antwortet; wählbar ``in_progress``) → ``in_progress`` (Familie
antwortet) … ``closed``. Eine Antwort der Familie auf ein geschlossenes Ticket öffnet es wieder – nur bis
``REOPEN_DAYS`` nach dem Schließen. ``waiting`` ohne Antwort der Familie: nach ``AUTO_CLOSE_DAYS`` geschlossen.
Interne Notizen sieht nur der Support; sie lösen keine Benachrichtigung aus.

Benachrichtigungen enthalten nie den Inhalt, nur Hinweis und Link.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, i18n
from .config import settings
from .i18n import N_, _
from .models import Device, Family, Operator, Parent, Ticket, TicketMessage, utcnow

log = logging.getLogger("kidctl.tickets")

CATEGORIES = {"tech": N_("Technik"), "billing": N_("Abo & Rechnung"), "other": N_("Sonstiges")}
STATUSES = {"new": N_("neu"), "in_progress": N_("in Bearbeitung"), "waiting": N_("wartend"), "closed": N_("geschlossen")}
REOPEN_DAYS = 14
AUTO_CLOSE_DAYS = 14
MAX_SUBJECT, MAX_BODY = 200, 10000
FAMILY = "Familie"


class TicketError(Exception):
    pass


def _set_status(db: Session, t: Ticket, status: str, actor) -> None:
    if status == t.status:
        return
    audit.record(db, actor, "ticket", "status", t.status, status, family_id=t.family_id, object_id=t.id)
    t.status = status
    t.closed_at = utcnow() if status == "closed" else None


def meta_for(db: Session, parent: Parent) -> str:
    """Beim Anlegen angehängt (für den Support): Agent-Versionen, Plattformen, Abo, Sprache."""
    rows = db.execute(select(Device.agent_version, Device.platform).where(Device.revoked_at.is_(None))).all()
    family = db.scalar(select(Family))
    return json.dumps({"agents": sorted({v for v, _p in rows if v}), "platforms": sorted({p for _v, p in rows}),
                       "plan": family.plan if family else "", "status": family.billing_status if family else "",
                       "paused": bool(family and family.paused_at), "language": i18n.parent_language(parent)})


def create(db: Session, parent: Parent, subject: str, category: str, body: str) -> Ticket:
    """Familie legt an (Familienkontext)."""
    subject, body = subject.strip()[:MAX_SUBJECT], body.strip()[:MAX_BODY]
    if not subject or not body or category not in CATEGORIES:
        raise TicketError(_("Bitte Betreff, Kategorie und Nachricht angeben."))
    t = Ticket(subject=subject, category=category, status="new", created_by=parent.id, meta=meta_for(db, parent))
    t.messages.append(TicketMessage(parent_id=parent.id, author=parent.display_name or parent.username, body=body))
    db.add(t)
    db.flush()
    audit.record(db, FAMILY, "ticket", "angelegt", None, CATEGORIES[category], family_id=t.family_id, object_id=t.id)
    db.commit()
    return t


def family_reply(db: Session, t: Ticket, parent: Parent, body: str) -> None:
    body = body.strip()[:MAX_BODY]
    if not body:
        raise TicketError(_("Bitte eine Nachricht eingeben."))
    if t.status == "closed" and (t.closed_at is None or utcnow() - t.closed_at > timedelta(days=REOPEN_DAYS)):
        raise TicketError(_("Dieses Ticket ist seit über {days} Tagen geschlossen. Bitte lege ein neues Ticket an.")
                          .format(days=REOPEN_DAYS))
    t.messages.append(TicketMessage(parent_id=parent.id, author=parent.display_name or parent.username, body=body))
    _set_status(db, t, "in_progress", FAMILY)
    t.updated_at = utcnow()
    db.commit()


def family_close(db: Session, t: Ticket) -> None:
    _set_status(db, t, "closed", FAMILY)
    t.updated_at = utcnow()
    db.commit()


def support_reply(db: Session, t: Ticket, actor: tuple, body: str, internal: bool, status: str) -> bool:
    """Antwort bzw. interne Notiz des Supports; Rückgabe: Familie benachrichtigen?"""
    body = body.strip()[:MAX_BODY]
    if not body:
        raise TicketError(_("Bitte eine Nachricht eingeben."))
    t.messages.append(TicketMessage(family_id=t.family_id, from_support=True, author=actor[0], internal=internal,
                                    body=body))   # Plattformkontext: Familie ausdrücklich
    if not internal:
        _set_status(db, t, status if status in ("waiting", "in_progress", "closed") else "waiting", actor)
    t.last_operator, t.updated_at = actor[0], utcnow()
    db.commit()
    return not internal


def support_status(db: Session, t: Ticket, actor: tuple, status: str) -> None:
    if status not in STATUSES:
        raise TicketError(_("Ungültiger Status."))
    _set_status(db, t, status, actor)
    t.last_operator, t.updated_at = actor[0], utcnow()
    db.commit()


def auto_close(db: Session, now=None) -> list[tuple[int, int]]:
    """Plattformkontext: wartende Tickets ohne Antwort der Familie seit 14 Tagen schließen. Rückgabe: (Familie, Ticket)."""
    now = now or utcnow()
    done = []
    for t in db.scalars(select(Ticket).where(Ticket.status == "waiting",
                                             Ticket.updated_at < now - timedelta(days=AUTO_CLOSE_DAYS))):
        _set_status(db, t, "closed", audit.SYSTEM)
        t.updated_at = now
        done.append((t.family_id, t.id))
    db.commit()
    return done


# ---------- Benachrichtigungen (ohne Inhalt) ----------

def notify_support(ticket_id: int, family_id: int, new: bool) -> None:
    """Neues Ticket bzw. Antwort der Familie → Betreiber mit „Ticket-Mails“ und SUPPORT_NOTIFY_EMAIL."""
    from .tenant import platform_session
    with platform_session() as db:
        to = set(db.scalars(select(Operator.email).where(Operator.ticket_mails.is_(True), Operator.email != "")))
    if settings.support_notify_email:
        to.add(settings.support_notify_email)
    link = f"{settings.base_url}/admin/tickets/{ticket_id}"
    from . import mailer
    for addr in sorted(to):
        mailer.send(addr, "ticket_support", "de", ticket_id=ticket_id, family_id=family_id, new=new, link=link)


def notify_family(family_id: int, ticket_id: int, closed: bool = False) -> None:
    """Antwort des Supports bzw. automatisches Schließen → alle Eltern der Familie per Mail und Push."""
    from .accounts import email_usable
    from .push import send_to_parents_sync
    from .tenant import family_session
    link = f"{settings.base_url}/support/{ticket_id}"
    subject_de = N_("Ticket #{id} geschlossen") if closed else N_("Neue Antwort auf Ticket #{id}")
    text_de = (N_("Euer Ticket wurde geschlossen, weil 14 Tage keine Antwort kam. Ihr könnt es noch 14 Tage lang "
                  "mit einer Antwort wieder öffnen.") if closed else N_("Der Support hat auf euer Ticket geantwortet."))
    with family_session(family_id) as db:
        for p in db.scalars(select(Parent)):
            if email_usable(p):
                from . import mailer
                mailer.send(p.email, "ticket_family", i18n.parent_language(p), name=p.display_name or p.username,
                            ticket_id=ticket_id, closed=closed, link=link)
    try:
        send_to_parents_sync(lambda: _(subject_de).format(id=ticket_id), lambda: _(text_de), f"/support/{ticket_id}",
                             tag=f"ticket-{ticket_id}", family_id=family_id)
    except Exception:
        log.exception("Push zu Ticket %s fehlgeschlagen", ticket_id)


async def run_forever() -> None:
    import asyncio
    from .tenant import platform_session
    while True:
        try:
            def run():
                with platform_session() as db:
                    return auto_close(db)
            for family_id, ticket_id in await asyncio.to_thread(run):
                await asyncio.to_thread(notify_family, family_id, ticket_id, True)
        except Exception:
            log.exception("Automatisches Schließen von Tickets fehlgeschlagen")
        await asyncio.sleep(3600)
