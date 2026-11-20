"""Webhook für einen späteren Zahlungsanbieter (Etappe 5) – neutral, bis der Anbieter feststeht.

``POST /api/billing/webhook`` mit ``Authorization: Bearer <BILLING_WEBHOOK_SECRET>`` und JSON:

    {"status": "active" | "canceled" | "past_due",
     "customer": "<Kundennummer beim Anbieter>",   # oder "family_id": 12 beim ersten Mal
     "provider": "<Name>", "paid_until": "2027-11-17"}   # optional

Ohne gesetztes Geheimnis gibt es den Endpunkt nicht (404). Die Wirkung ist dieselbe wie eine Änderung im
Verwaltungsbereich und tritt sofort ein (Ruhemodus endet bzw. Fristen laufen).
"""

from __future__ import annotations

import hmac
import logging
from datetime import date, datetime, time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from .. import billing
from ..config import settings
from ..models import Family
from ..tenant import platform_session

router = APIRouter()
log = logging.getLogger("kidctl.billing")


class WebhookEvent(BaseModel):
    status: str = Field(pattern="^(active|canceled|past_due)$")
    customer: str = Field("", max_length=128)
    family_id: int | None = None
    provider: str = Field("", max_length=32)
    paid_until: date | None = None
    plan: str = Field("standard", pattern="^(free|standard)$")


@router.post("/api/billing/webhook")
async def webhook(request: Request):
    secret = settings.billing_webhook_secret
    if not secret:
        raise HTTPException(404)
    auth = request.headers.get("authorization", "")
    if not hmac.compare_digest(auth.encode(), f"Bearer {secret}".encode()):
        raise HTTPException(401, "Ungültiges Geheimnis")
    try:   # Body erst nach dem Geheimnis auswerten
        event = WebhookEvent.model_validate_json(await request.body())
    except ValidationError as e:
        raise HTTPException(422, "Ungültige Daten") from e
    with platform_session() as db:
        f = None
        if event.customer:
            f = db.scalar(select(Family).where(Family.billing_customer == event.customer,
                                               Family.billing_provider == (event.provider or Family.billing_provider)))
        if f is None and event.family_id is not None:
            f = db.get(Family, event.family_id)
        if f is None or f.wrapped_key is None:
            raise HTTPException(404, "Familie nicht gefunden")
        from .. import audit
        before = audit.snapshot(f, billing.AUDIT_FIELDS)
        if event.customer:
            f.billing_customer = event.customer
        if event.provider:
            f.billing_provider = event.provider
        f.plan = event.plan
        paid = datetime.combine(event.paid_until, time(23, 59)) if event.paid_until else None
        if event.status == "active" and paid is None:
            f.paid_until = None   # aktiv ohne Datum = ohne Ende
        billing.set_status(f, event.status, paid)
        audit.changes(db, audit.WEBHOOK, "subscription", before, f, family_id=f.id)
        family_id = f.id
        db.commit()
    log.info("Webhook: Familie %s → %s (bis %s)", family_id, event.status, event.paid_until)
    result = await billing.apply(family_id)
    return {"ok": True, "family_id": family_id, "result": result or "unchanged"}
