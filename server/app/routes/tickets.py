"""Support-Tickets für Eltern (nur SaaS): Menüpunkt „Support“ – Liste, Anlegen, Verlauf, Antworten, Schließen.
Auch im Ruhemodus erreichbar. Interne Notizen des Supports sind hier nie sichtbar."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import tickets
from ..config import settings
from ..db import get_db
from ..models import Parent, ParentSession, Ticket, TicketMessage
from ..security import csrf_protect, require_session
from ..web import local_redirect, render

router = APIRouter(prefix="/support")


def _saas() -> None:
    if not settings.saas:
        raise HTTPException(404)


def _ticket(db: Session, ticket_id: int) -> Ticket:
    t = db.scalar(select(Ticket).where(Ticket.id == ticket_id))   # Familienkontext filtert fremde Tickets aus
    if t is None:
        raise HTTPException(404)
    return t


def _page(request, db, t, status_code=200, **ctx):
    messages = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t.id,
                                                      TicketMessage.internal.is_(False))
                          .order_by(TicketMessage.id)).all()
    return render(request, "ticket.html", status_code=status_code, t=t, messages=messages,
                  categories=tickets.CATEGORIES, statuses=tickets.STATUSES, reopen_days=tickets.REOPEN_DAYS, **ctx)


@router.get("", dependencies=[Depends(_saas)])
def ticket_list(request: Request, sess: ParentSession = Depends(require_session), db: Session = Depends(get_db)):
    rows = db.scalars(select(Ticket).order_by(Ticket.status == "closed", Ticket.updated_at.desc())).all()
    return render(request, "tickets.html", tickets=rows, categories=tickets.CATEGORIES, statuses=tickets.STATUSES,
                  form={})


@router.post("", dependencies=[Depends(_saas)])
def ticket_create(request: Request, background: BackgroundTasks, subject: str = Form(""), category: str = Form(""),
                  body: str = Form(""), sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    try:
        t = tickets.create(db, parent, subject, category, body)
    except tickets.TicketError as e:
        rows = db.scalars(select(Ticket).order_by(Ticket.updated_at.desc())).all()
        return render(request, "tickets.html", status_code=400, tickets=rows, categories=tickets.CATEGORIES,
                      statuses=tickets.STATUSES, error=str(e),
                      form={"subject": subject, "category": category, "body": body})
    background.add_task(tickets.notify_support, t.id, t.family_id, True)
    return local_redirect(f"/support/{t.id}", status_code=303)


@router.get("/{ticket_id}", dependencies=[Depends(_saas)])
def ticket_view(ticket_id: int, request: Request, sess: ParentSession = Depends(require_session),
                db: Session = Depends(get_db)):
    return _page(request, db, _ticket(db, ticket_id))


@router.post("/{ticket_id}", dependencies=[Depends(_saas)])
def ticket_reply(ticket_id: int, request: Request, background: BackgroundTasks, body: str = Form(""),
                 sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    t = _ticket(db, ticket_id)
    try:
        tickets.family_reply(db, t, db.get(Parent, sess.parent_id), body)
    except tickets.TicketError as e:
        return _page(request, db, t, 400, error=str(e), body=body)
    background.add_task(tickets.notify_support, t.id, t.family_id, False)
    return local_redirect(f"/support/{t.id}#ende", status_code=303)


@router.post("/{ticket_id}/close", dependencies=[Depends(_saas)])
def ticket_close(ticket_id: int, sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    tickets.family_close(db, _ticket(db, ticket_id))
    return local_redirect(f"/support/{ticket_id}", status_code=303)
