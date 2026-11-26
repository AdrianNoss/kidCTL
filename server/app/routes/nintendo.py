"""Einstellungen → Nintendo Switch (experimentell): verknüpfen, Profile zuordnen, trennen (alle Eltern)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import nintendo
from ..config import settings
from ..db import get_db
from ..i18n import N_
from ..models import ParentSession
from ..security import csrf_protect

router = APIRouter()

ERRORS = {
    "expired": N_("Der Login-Link ist abgelaufen. Bitte einen neuen Link erzeugen."),
    "format": N_("Das ist nicht der kopierte Link. Er beginnt mit npf54789befb391a838://auth – bitte den Link hinter „Diese Person auswählen“ kopieren, nicht die Adresse der Seite."),
    "rejected": N_("Nintendo hat die Anmeldung abgelehnt. Der Link gilt nur kurz und nur einmal – bitte einen neuen Link erzeugen und von vorn beginnen."),
    "network": N_("Nintendo ist gerade nicht erreichbar. Bitte später erneut versuchen."),
}


def _enabled() -> None:
    if not settings.nintendo:
        raise HTTPException(404)


def _back(saved: str = "nintendo") -> RedirectResponse:
    return RedirectResponse(f"/settings?saved={saved}#nintendo", status_code=303)


def _error(request: Request, db: Session, sess: ParentSession, code: str):
    from ..i18n import _
    from .pwa import _settings
    return _settings(request, db, sess, status_code=400, nintendo_error=_(ERRORS[code]))


@router.post("/settings/nintendo/start", dependencies=[Depends(_enabled)])
async def nintendo_start(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    await nintendo.start_link(db)
    db.commit()
    return RedirectResponse("/settings#nintendo", status_code=303)


@router.post("/settings/nintendo/link", dependencies=[Depends(_enabled)])
async def nintendo_link(request: Request, url: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                        db: Session = Depends(get_db)):
    try:
        await nintendo.complete_link(db, url)
    except nintendo.LinkError as e:
        db.rollback()
        return _error(request, db, sess, str(e))
    db.commit()
    return _back("nintendo_linked")


@router.post("/settings/nintendo/assign", dependencies=[Depends(_enabled)])
def nintendo_assign(player_id: int = Form(...), child_id: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                    db: Session = Depends(get_db)):
    try:
        nintendo.assign(db, player_id, int(child_id) if child_id.strip() else None)
    except (LookupError, ValueError):
        raise HTTPException(404)
    db.commit()
    return _back()


@router.post("/settings/nintendo/unlink", dependencies=[Depends(_enabled)])
def nintendo_unlink(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    nintendo.unlink(db)
    db.commit()
    return _back()
