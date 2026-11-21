"""Verwaltungsbereich: Verwaltungsprotokoll."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import operators as opr
from ..db import get_platform_db
from ..i18n import N_
from ..models import OperatorSession
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Verwaltungsprotokoll ----------

LOG_PAGE = 50
LOG_OBJECTS = {"family": N_("Familie"), "subscription": N_("Abo"), "code": N_("Einladungscode"),
               "ticket": N_("Ticket"), "invoice": N_("Rechnung"), "invoice_settings": N_("Rechnungs-Einstellungen"),
               "price": N_("Preisliste"), "operator": N_("Betreiber-Zugang"),
               "branding": N_("Erscheinungsbild"), "newsletter": N_("Newsletter")}


def log_query(family: str = "", actor: str = "", obj: str = ""):
    from ..models import AdminLog
    q = select(AdminLog)
    if family.strip().lstrip("#").isdigit():
        q = q.where(AdminLog.family_id == int(family.strip().lstrip("#")))
    if actor.strip():
        q = q.where(AdminLog.actor.ilike(actor.strip()))
    if obj in LOG_OBJECTS:
        q = q.where(AdminLog.object == obj)
    return q


@router.get("/log")
def admin_log(request: Request, family: str = "", actor: str = "", obj: str = "", page: int = 1,
              sess: OperatorSession = Depends(opr.require_operator), db: Session = Depends(get_platform_db)):
    from ..models import AdminLog
    q = log_query(family, actor, obj)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    page = max(1, page)
    entries = db.scalars(q.order_by(AdminLog.at.desc(), AdminLog.id.desc())
                         .offset((page - 1) * LOG_PAGE).limit(LOG_PAGE)).all()
    actors = sorted(db.scalars(select(AdminLog.actor).distinct()))
    from urllib.parse import urlencode
    params = {k: v for k, v in {"family": family, "actor": actor, "obj": obj}.items() if v}
    return _render(request, "admin_log.html", sess, entries=entries, total=total, page=page,
                   pages=max(1, -(-total // LOG_PAGE)), objects=LOG_OBJECTS, actors=actors,
                   f={"family": family, "actor": actor, "obj": obj}, query=urlencode(params))
