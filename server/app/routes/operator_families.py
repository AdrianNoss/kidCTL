"""Verwaltungsbereich: Familienübersicht, Familienseite (Name, Abo) und Support-Ansicht."""

from __future__ import annotations

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit, backup, billing, invoices, support, tickets
from ..config import settings
from ..db import get_platform_db
from ..i18n import _
from ..models import OperatorSession
from ..web import local_redirect
from .operator_base import new_router, render as _render
from .operator_log import LOG_OBJECTS

router = new_router()


# ---------- Übersicht ----------

@router.get("")
def overview(request: Request, q: str = "", status: str = "", plan: str = "", issue: str = "", since: str = "",
             until: str = "", idle: str = "", sort: str = "id", page: int = 1,
             sess: OperatorSession = Depends(opr.require_operator), db: Session = Depends(get_platform_db)):
    """Familien: Kacheln, Suche, Filter (einfach und „Erweitert“), Sortierung, 50 je Seite – alles im Server,
    Parameter in der URL."""
    from urllib.parse import urlencode
    since = since if opr.parse_day(since) else ""
    until = until if opr.parse_day(until) else ""
    params = {k: v for k, v in {"q": q.strip(), "status": status, "plan": plan, "issue": issue, "since": since,
                                "until": until, "idle": idle}.items() if v}

    def url(**change):
        merged = {**params, "sort": sort, "page": 1, **change}
        merged = {k: v for k, v in merged.items() if v not in ("", None) and not (k == "sort" and v == "id")
                  and not (k == "page" and v == 1)}
        return "/admin" + (f"?{urlencode(merged)}" if merged else "")

    # Das Suchformular schickt auch leere Felder mit – auf eine saubere Adresse umleiten (teilbar, lesbar)
    if any(not v for v in request.query_params.values()):
        return RedirectResponse(url(page=page), status_code=303)

    rows = opr.families(db)
    backups = backup.backups()
    tiles = {"families": len(rows), "children": sum(r.children for r in rows), "devices": sum(r.devices for r in rows),
             "trial": sum(1 for r in rows if r.status == "trial"),
             "trial_ending": sum(1 for r in rows if r.trial_ending),
             "paused": sum(1 for r in rows if r.status == "paused"),
             "tickets": sum(r.tickets for r in rows)}
    selected = opr.select_rows(rows, q, status, plan, issue, sort, since=since, until=until, idle=idle)
    pages = max(1, -(-len(selected) // opr.PAGE_SIZE))
    page = min(max(1, page), pages)

    def sort_url(key):
        return url(sort=f"-{key}" if sort == key else key)

    return _render(request, "admin_overview.html", sess, families=selected[(page - 1) * opr.PAGE_SIZE:page * opr.PAGE_SIZE],
                   total=len(selected), all_total=len(rows), tiles=tiles, page=page, pages=pages, url=url,
                   sort_url=sort_url, sort=sort, f={"q": q.strip(), "status": status, "plan": plan, "issue": issue,
                                                    "since": since, "until": until, "idle": idle},
                   parse_day=opr.parse_day, backups=backups, backup_error=backup.last_error(), plans=billing.PLANS, statuses=billing.STATUSES,
                   granted=support.granted_families(db))


# ---------- Abo einer Familie ----------

EDIT_CARDS = ("name", "abo")


def _family_page(request, db, sess, family_id, status_code=200, edit: str = "", **ctx):
    """Detailansicht: Karten Kopf, Abo, Rechnungsangaben, Nutzung, Support, Rechnungen, Verlauf (nur Metadaten außer
    Name und Rechnungsangaben). ``edit`` = Karte als Formular (nur Admin)."""
    from ..models import AdminLog, BillingAddress, Device, Family, SupportAccess, SupportGrant
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    rows = opr.families(db, only=family_id)
    devices = db.execute(select(Device.id, Device.platform, Device.agent_version, Device.last_seen_at,
                                Device.revoked_at, Device.protocol_unsupported)
                         .where(Device.family_id == family_id).order_by(Device.revoked_at.is_not(None), Device.id)).all()
    accesses = db.execute(select(SupportAccess.at, SupportAccess.operator, SupportAccess.path)
                          .where(SupportAccess.family_id == family_id)
                          .order_by(SupportAccess.at.desc()).limit(10)).all()
    last_grant = db.execute(select(SupportGrant.created_at, SupportGrant.expires_at, SupportGrant.revoked_at)
                            .where(SupportGrant.family_id == family_id)
                            .order_by(SupportGrant.created_at.desc()).limit(1)).first()
    history = db.scalars(select(AdminLog).where(AdminLog.family_id == family_id)
                         .order_by(AdminLog.at.desc(), AdminLog.id.desc()).limit(15)).all()
    from ..models import Ticket
    family_tickets = db.scalars(select(Ticket).where(Ticket.family_id == family_id)
                                .order_by(Ticket.status == "closed", Ticket.updated_at.desc()).limit(10)).all()
    is_admin = sess.operator.role == "admin"
    from ..models import Invoice, PriceItem
    family_invoices = db.scalars(select(Invoice).where(Invoice.family_id == family_id)
                                 .order_by(Invoice.created_at.desc())).all() if is_admin else []
    price_items = db.scalars(select(PriceItem).where(PriceItem.active.is_(True)).order_by(PriceItem.id)).all() \
        if is_admin else []
    return _render(request, "admin_family.html", sess, status_code, f=f, row=rows[0] if rows else None,
                   family_invoices=family_invoices, price_items=price_items, eur=invoices.eur,
                   invoice_statuses=invoices.STATUSES,
                   family_tickets=family_tickets, ticket_statuses=tickets.STATUSES,
                   st=billing.state(f), devices=devices, accesses=accesses, last_grant=last_grant, history=history,
                   address=db.scalar(select(BillingAddress).where(BillingAddress.family_id == family_id)),
                   granted=support.granted_families(db).get(family_id), is_admin=is_admin,
                   edit=edit if is_admin and edit in EDIT_CARDS else "", log_objects=LOG_OBJECTS,
                   plans=billing.PLANS, statuses=billing.STATUSES, **ctx)


@router.get("/families/{family_id}")
def family_detail(family_id: int, request: Request, edit: str = "",
                  sess: OperatorSession = Depends(opr.require_operator), db: Session = Depends(get_platform_db)):
    return _family_page(request, db, sess, family_id, edit=edit, saved=request.query_params.get("saved"))


def _date(value: str):
    from datetime import date, datetime, time
    value = value.strip()
    return datetime.combine(date.fromisoformat(value), time(23, 59)) if value else None


@router.post("/families/{family_id}/name")
def family_name(family_id: int, name: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    before = audit.snapshot(f, ["name"])
    f.name = name.strip()[:128]
    audit.changes(db, audit.actor_of(sess), "family", before, f, family_id=family_id)
    db.commit()
    return local_redirect(f"/admin/families/{family_id}?saved=1#kopf", status_code=303)


@router.post("/families/{family_id}")
async def family_billing(family_id: int, request: Request, plan: str = Form(...), status: str = Form(...),
                         trial_ends: str = Form(""), paid_until: str = Form(""), provider: str = Form(""),
                         customer: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                         db: Session = Depends(get_platform_db)):
    """Tarif und Abo-Status von Hand setzen (bis ein Zahlungsanbieter angebunden ist); wirkt sofort."""
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    try:
        if plan not in billing.PLANS or status not in billing.STATUSES:
            raise ValueError
        trial, paid = _date(trial_ends), _date(paid_until)
    except ValueError:
        return _family_page(request, db, sess, family_id, 400, edit="abo", error=_("Ungültige Eingabe."))
    before = audit.snapshot(f, billing.AUDIT_FIELDS)
    f.plan, f.trial_ends_at, f.paid_until = plan, trial, paid
    f.billing_provider, f.billing_customer = provider.strip()[:32], customer.strip()[:128]
    billing.set_status(f, status, paid)
    audit.changes(db, audit.actor_of(sess), "subscription", before, f, family_id=family_id)
    db.commit()
    import logging
    logging.getLogger("kidctl.billing").info("Familie %s: Abo gesetzt von %s (%s, %s)", family_id,
                                              sess.operator.username, plan, status)
    await billing.apply(family_id)
    return local_redirect(f"/admin/families/{family_id}?saved=1#abo", status_code=303)


# ---------- Support-Ansicht (nur mit Freigabe der Familie) ----------

@router.post("/support/{family_id}")
def support_open(family_id: int, sess: OperatorSession = Depends(opr.operator_csrf),
                 db: Session = Depends(get_platform_db)):
    raw = support.open_view(db, sess, family_id)
    if raw is None:
        raise HTTPException(403, "Keine Freigabe dieser Familie")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(support.COOKIE, raw, max_age=support.VIEW_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/")
    return resp


@router.get("/support/end")
def support_end(request: Request, db: Session = Depends(get_platform_db)):
    """Support-Ansicht beenden (Link im Banner); ohne Betreiber-Anmeldung nötig, löscht nur die eigene Ansicht."""
    support.close_view(db, request)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(support.COOKIE, path="/")
    return resp
