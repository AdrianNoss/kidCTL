"""Verwaltungsbereich: Support-Tickets."""

from __future__ import annotations

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit, tickets
from ..db import get_platform_db
from ..models import OperatorSession
from ..web import local_redirect
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Tickets ----------

TICKET_SORTS = {"created": lambda t: t.created_at, "updated": lambda t: t.updated_at, "id": lambda t: t.id}


@router.get("/tickets")
def ticket_list(request: Request, status: str = "open", category: str = "", family: str = "", q: str = "",
                sort: str = "created", page: int = 1, sess: OperatorSession = Depends(opr.require_operator),
                db: Session = Depends(get_platform_db)):
    """Alle Tickets: Filter Status (Standard „offen“ = alles außer geschlossen), Kategorie, Familie, Freitext im
    Betreff (entschlüsselt im Server), älteste zuerst; 50 je Seite, Parameter in der URL."""
    from urllib.parse import urlencode
    from ..models import Family, Ticket
    query = select(Ticket)
    if status == "open":
        query = query.where(Ticket.status != "closed")
    elif status in tickets.STATUSES:
        query = query.where(Ticket.status == status)
    if category in tickets.CATEGORIES:
        query = query.where(Ticket.category == category)
    if family.strip().lstrip("#").isdigit():
        query = query.where(Ticket.family_id == int(family.strip().lstrip("#")))
    rows = db.scalars(query).all()
    if q.strip():
        needle = q.strip().casefold()
        rows = [t for t in rows if needle in t.subject.casefold()]
    key = sort.lstrip("-")
    rows.sort(key=TICKET_SORTS.get(key, TICKET_SORTS["created"]), reverse=sort.startswith("-"))
    pages = max(1, -(-len(rows) // opr.PAGE_SIZE))
    page = min(max(1, page), pages)
    shown = rows[(page - 1) * opr.PAGE_SIZE:page * opr.PAGE_SIZE]
    names = dict(db.execute(select(Family.id, Family.name).where(Family.id.in_({t.family_id for t in shown}))).all()) \
        if shown else {}
    params = {k: v for k, v in {"status": status, "category": category, "family": family, "q": q.strip(),
                                "sort": sort}.items() if v and not (k == "status" and v == "open")
              and not (k == "sort" and v == "created")}

    def url(**change):
        merged = {**params, "page": 1, **change}
        merged = {k: v for k, v in merged.items() if v not in ("", None) and not (k == "page" and v == 1)}
        return "/admin/tickets" + (f"?{urlencode(merged)}" if merged else "")
    return _render(request, "admin_tickets.html", sess, tickets=shown, total=len(rows), page=page, pages=pages,
                   url=url, names=names, categories=tickets.CATEGORIES, statuses=tickets.STATUSES,
                   f={"status": status, "category": category, "family": family, "q": q, "sort": sort})


def _admin_ticket(db: Session, ticket_id: int):
    from ..models import Ticket
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404)
    return t


def _ticket_page(request, db, sess, t, status_code=200, **ctx):
    import json
    from ..models import Family
    family = db.get(Family, t.family_id)
    try:
        meta = json.loads(t.meta) if t.meta else {}
    except ValueError:
        meta = {}
    return _render(request, "admin_ticket.html", sess, status_code, t=t, family=family, meta=meta,
                   categories=tickets.CATEGORIES, statuses=tickets.STATUSES, **ctx)


@router.get("/tickets/{ticket_id}")
def ticket_view(ticket_id: int, request: Request, sess: OperatorSession = Depends(opr.require_operator),
                db: Session = Depends(get_platform_db)):
    return _ticket_page(request, db, sess, _admin_ticket(db, ticket_id))


@router.post("/tickets/{ticket_id}")
def ticket_reply(ticket_id: int, request: Request, background: BackgroundTasks, body: str = Form(""),
                 internal: str = Form(""), status: str = Form("waiting"),
                 sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    t = _admin_ticket(db, ticket_id)
    try:
        notify = tickets.support_reply(db, t, audit.actor_of(sess), body, bool(internal), status)
    except tickets.TicketError as e:
        return _ticket_page(request, db, sess, t, 400, error=str(e), body=body)
    if notify:
        background.add_task(tickets.notify_family, t.family_id, t.id, False)
    return local_redirect(f"/admin/tickets/{t.id}#ende", status_code=303)


@router.post("/tickets/{ticket_id}/status")
def ticket_status(ticket_id: int, request: Request, status: str = Form(""),
                  sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    t = _admin_ticket(db, ticket_id)
    try:
        tickets.support_status(db, t, audit.actor_of(sess), status)
    except tickets.TicketError as e:
        return _ticket_page(request, db, sess, t, 400, error=str(e))
    return local_redirect(f"/admin/tickets/{t.id}", status_code=303)
