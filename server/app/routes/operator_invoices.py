"""Verwaltungsbereich: Rechnungen und Rechnungs-Einstellungen (nur Admin)."""

from __future__ import annotations

from datetime import date as _date_type

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit, billing, invoices
from ..config import settings
from ..db import get_platform_db
from ..i18n import _
from ..models import OperatorSession
from ..web import local_redirect
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Rechnungen (nur Admin) ----------

SETTING_FIELDS = {"sender_name": 128, "sender_street": 128, "sender_postal_code": 16, "sender_city": 64,
                  "sender_country": 64, "tax_id": 64, "contact": 255, "bank_holder": 128, "iban": 64, "bic": 16,
                  "footer": 2000}


def _invoice(db: Session, invoice_id: int):
    from ..models import Invoice
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404)
    return inv


@router.get("/invoices")
def invoice_list(request: Request, year: str = "", status: str = "", family: str = "", page: int = 1,
                 sess: OperatorSession = Depends(opr.require_admin), db: Session = Depends(get_platform_db)):
    from urllib.parse import urlencode
    from ..models import Invoice
    q = select(Invoice)
    if year.isdigit():
        q = q.where(Invoice.year == int(year))
    if status in invoices.STATUSES:
        q = q.where(Invoice.status == status)
    if family.strip().lstrip("#").isdigit():
        q = q.where(Invoice.family_id == int(family.strip().lstrip("#")))
    rows = db.scalars(q.order_by(Invoice.status != "draft", Invoice.year.desc(), Invoice.seq.desc(),
                                 Invoice.id.desc())).all()
    pages = max(1, -(-len(rows) // opr.PAGE_SIZE))
    page = min(max(1, page), pages)
    sums = db.execute(select(Invoice.year, func.count(), func.sum(Invoice.net_cents), func.sum(Invoice.tax_cents),
                             func.sum(Invoice.gross_cents)).where(Invoice.year.is_not(None))
                      .group_by(Invoice.year).order_by(Invoice.year.desc())).all()
    params = {k: v for k, v in {"year": year, "status": status, "family": family}.items() if v}

    def url(**change):
        merged = {k: v for k, v in {**params, "page": 1, **change}.items() if v not in ("", None) and not (k == "page" and v == 1)}
        return "/admin/invoices" + (f"?{urlencode(merged)}" if merged else "")
    return _render(request, "admin_invoices.html", sess, invoices=rows[(page - 1) * opr.PAGE_SIZE:page * opr.PAGE_SIZE],
                   total=len(rows), page=page, pages=pages, url=url, sums=sums, statuses=invoices.STATUSES,
                   eur=invoices.eur, f={"year": year, "status": status, "family": family},
                   settings_missing=invoices.missing_settings(invoices.settings_row(db)))


@router.get("/invoices/export/{year}.csv")
def invoice_export_csv(year: int, sess: OperatorSession = Depends(opr.require_admin),
                       db: Session = Depends(get_platform_db)):
    from fastapi.responses import Response
    return Response(invoices.export_csv(db, year).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rechnungen-{year}.csv"'})


@router.get("/invoices/export/{year}.zip")
def invoice_export_zip(year: int, sess: OperatorSession = Depends(opr.require_admin),
                       db: Session = Depends(get_platform_db)):
    from fastapi.responses import Response
    return Response(invoices.export_zip(db, year), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="rechnungen-{year}.zip"'})


def _settings_page(request, db, sess, status_code=200, **ctx):
    from ..models import PriceItem
    row = invoices.settings_row(db)
    db.commit()
    return _render(request, "admin_invoice_settings.html", sess, status_code, s=row,
                   missing=invoices.missing_settings(row), prices=db.scalars(select(PriceItem).order_by(PriceItem.id)).all(),
                   eur=invoices.eur, tax_mode=settings.invoice_tax_mode, number_format=settings.invoice_number_format,
                   due_days=settings.invoice_due_days, **ctx)


@router.get("/invoice-settings")
def invoice_settings(request: Request, sess: OperatorSession = Depends(opr.require_admin),
                     db: Session = Depends(get_platform_db)):
    return _settings_page(request, db, sess, saved=request.query_params.get("saved"))


@router.post("/invoice-settings")
async def invoice_settings_save(request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                                db: Session = Depends(get_platform_db)):
    form = await request.form()
    row = invoices.settings_row(db)
    before = audit.snapshot(row, list(SETTING_FIELDS))
    for field, n in SETTING_FIELDS.items():
        value = str(form.get(field, "")).strip()[:n]
        if field == "iban":
            value = value.replace(" ", "").upper()
        setattr(row, field, value)
    audit.changes(db, audit.actor_of(sess), "invoice_settings", before, row)
    db.commit()
    return RedirectResponse("/admin/invoice-settings?saved=1", status_code=303)


@router.post("/invoice-settings/prices")
def price_save(request: Request, item_id: str = Form(""), label: str = Form(""), amount: str = Form(""),
               months: str = Form("12"), active: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
               db: Session = Depends(get_platform_db)):
    from ..models import PriceItem
    try:
        cents, n = invoices.parse_eur(amount), int(months)
        if not label.strip() or n < 1 or n > 120:
            raise ValueError
    except ValueError:
        return _settings_page(request, db, sess, 400, price_error=_("Bitte Bezeichnung, Betrag und Laufzeit (1–120 Monate) angeben."))
    item = db.get(PriceItem, int(item_id)) if item_id.isdigit() else None
    if item is None:
        item = PriceItem(label="", amount_cents=0)
        db.add(item)
        db.flush()
        before = {"label": None, "amount_cents": None, "months": None, "active": None}
    else:
        before = audit.snapshot(item, ["label", "amount_cents", "months", "active"])
    item.label, item.amount_cents, item.months, item.active = label.strip()[:200], cents, n, bool(active)
    audit.changes(db, audit.actor_of(sess), "price", before, item, object_id=item.id)
    db.commit()
    return RedirectResponse("/admin/invoice-settings?saved=1#preise", status_code=303)


@router.post("/families/{family_id}/invoices")
def invoice_create(family_id: int, price_item: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                   db: Session = Depends(get_platform_db)):
    from ..models import Family, PriceItem
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    item = db.get(PriceItem, int(price_item)) if price_item.isdigit() else None
    inv = invoices.create_draft(db, f, item, audit.actor_of(sess))
    return local_redirect(f"/admin/invoices/{inv.id}", status_code=303)


def _invoice_page(request, db, sess, inv, status_code=200, **ctx):
    import json
    from ..models import Family, Invoice
    family = db.get(Family, inv.family_id)
    related = db.scalars(select(Invoice).where((Invoice.storno_of == inv.id) | (Invoice.id == inv.storno_of))).all()
    return _render(request, "admin_invoice.html", sess, status_code, inv=inv, family=family, related=related,
                   problems=invoices.issue_problems(db, inv) if inv.status == "draft" else [],
                   address=json.loads(inv.address) if inv.address else None, eur=invoices.eur,
                   preview=invoices.totals(inv.lines, inv.tax_mode or settings.invoice_tax_mode),
                   statuses=invoices.STATUSES, expires=invoices.expires_on(inv), today=invoices.today(), **ctx)


@router.get("/invoices/{invoice_id}")
def invoice_view(invoice_id: int, request: Request, sess: OperatorSession = Depends(opr.require_admin),
                 db: Session = Depends(get_platform_db)):
    return _invoice_page(request, db, sess, _invoice(db, invoice_id), saved=request.query_params.get("saved"))


@router.post("/invoices/{invoice_id}")
async def invoice_save(invoice_id: int, request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                       db: Session = Depends(get_platform_db)):
    """Entwurf speichern (Leistungszeitraum, Positionen). Ausgestellte Rechnungen: 409."""
    inv = _invoice(db, invoice_id)
    if inv.status != "draft":
        raise HTTPException(409, "Ausgestellte Rechnungen sind unveränderlich")
    form = await request.form()
    try:
        start = _date_type.fromisoformat(str(form.get("service_start", ""))) if form.get("service_start") else None
        end = _date_type.fromisoformat(str(form.get("service_end", ""))) if form.get("service_end") else None
        lines = []
        for text, qty, price in zip(form.getlist("line_text"), form.getlist("line_qty"), form.getlist("line_price")):
            if not str(text).strip():
                continue
            lines.append((str(text).strip(), int(qty or 1), invoices.parse_eur(str(price))))
        invoices.update_draft(db, inv, start, end, lines)
    except (ValueError, invoices.InvoiceError) as e:
        db.rollback()
        msg = str(e) if isinstance(e, invoices.InvoiceError) else _("Bitte Datum, Menge und Betrag prüfen.")
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=msg)
    return local_redirect(f"/admin/invoices/{inv.id}?saved=1", status_code=303)


@router.post("/invoices/{invoice_id}/delete")
def invoice_delete(invoice_id: int, sess: OperatorSession = Depends(opr.admin_csrf),
                   db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    family_id = inv.family_id
    try:
        invoices.delete_draft(db, inv, audit.actor_of(sess))
    except invoices.InvoiceError as e:
        raise HTTPException(409, str(e))
    return local_redirect(f"/admin/families/{family_id}#rechnungen", status_code=303)


def _send_later(invoice_id: int, actor: tuple, again: bool) -> None:
    from ..models import Invoice
    from ..tenant import platform_session
    with platform_session() as db:
        inv = db.get(Invoice, invoice_id)
        try:
            if inv is not None:
                invoices.send(db, inv, actor, again)
        except invoices.InvoiceError as e:
            import logging
            logging.getLogger("kidctl.invoices").warning("Rechnung %s nicht gesendet: %s", invoice_id, e)


@router.post("/invoices/{invoice_id}/issue")
def invoice_issue(invoice_id: int, request: Request, background: BackgroundTasks,
                  sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    try:
        invoices.issue(db, inv, audit.actor_of(sess))
    except invoices.InvoiceError as e:
        db.rollback()
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=str(e))
    background.add_task(_send_later, inv.id, audit.actor_of(sess), False)
    return local_redirect(f"/admin/invoices/{inv.id}?saved=issued", status_code=303)


@router.post("/invoices/{invoice_id}/paid")
async def invoice_paid(invoice_id: int, request: Request, paid_on: str = Form(""),
                       sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    try:
        day = _date_type.fromisoformat(paid_on) if paid_on else invoices.today()
        family_id = invoices.mark_paid(db, inv, day, audit.actor_of(sess))
    except (ValueError, invoices.InvoiceError) as e:
        db.rollback()
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=str(e))
    if family_id:
        await billing.apply(family_id)          # Ruhemodus endet sofort, Policies an die Agenten
    return local_redirect(f"/admin/invoices/{inv.id}?saved=paid", status_code=303)


@router.post("/invoices/{invoice_id}/storno")
def invoice_storno(invoice_id: int, request: Request, background: BackgroundTasks,
                   sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    was_paid = inv.status == "paid"
    try:
        s = invoices.storno(db, inv, audit.actor_of(sess))
    except invoices.InvoiceError as e:
        db.rollback()
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=str(e))
    background.add_task(_send_later, s.id, audit.actor_of(sess), False)
    return local_redirect(f"/admin/invoices/{s.id}?saved={'storno_paid' if was_paid else 'storno'}", status_code=303)


@router.post("/invoices/{invoice_id}/resend")
def invoice_resend(invoice_id: int, request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                   db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    try:
        ok = invoices.send(db, inv, audit.actor_of(sess), again=True)
    except invoices.InvoiceError as e:
        return _invoice_page(request, db, sess, inv, 400, error=str(e))
    if not ok:
        return _invoice_page(request, db, sess, inv, 502, error=_("Mail konnte nicht gesendet werden (SMTP prüfen)."))
    return local_redirect(f"/admin/invoices/{inv.id}?saved=sent", status_code=303)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, sess: OperatorSession = Depends(opr.require_admin),
                db: Session = Depends(get_platform_db)):
    from fastapi.responses import Response
    inv = _invoice(db, invoice_id)
    if inv.status == "draft":
        raise HTTPException(404)
    return Response(invoices.pdf_bytes(inv), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv.number}.pdf"', "Cache-Control": "no-store"})
