"""„Feature vorschlagen“ (``/feature-request``): nur mit ``FEATURE_REQUEST_EMAIL`` und SMTP, sonst 404."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .. import feature_requests as fr
from ..accounts import email_usable
from ..config import settings
from ..db import get_db
from ..models import Parent, ParentSession
from ..security import csrf_protect, require_session
from ..web import local_redirect, render

router = APIRouter(prefix="/feature-request")


def _enabled() -> None:
    if not settings.feature_requests:
        raise HTTPException(404)


def _page(request: Request, db: Session, sess, status_code: int = 200, **ctx):
    parent = db.get(Parent, sess.parent_id) if sess.parent_id else None
    return render(request, "feature_request.html", status_code=status_code, categories=fr.CATEGORIES,
                  importance=fr.IMPORTANCE, max_title=fr.MAX_TITLE, max_description=fr.MAX_DESCRIPTION, daily_limit=fr.DAILY_LIMIT,
                  has_email=bool(parent and email_usable(parent)), sent=request.query_params.get("sent"),
                  **{"form": {}} | ctx)


@router.get("", dependencies=[Depends(_enabled)])
def form_page(request: Request, sess: ParentSession = Depends(require_session), db: Session = Depends(get_db)):
    return _page(request, db, sess)


@router.post("", dependencies=[Depends(_enabled)])
def submit(request: Request, title: str = Form(""), description: str = Form(""), category: str = Form(""),
           importance: str = Form(""), contact: str = Form(""), sess: ParentSession = Depends(csrf_protect),
           db: Session = Depends(get_db)):
    try:
        fr.submit(db, db.get(Parent, sess.parent_id), title=title, description=description, category=category,
                  importance=importance, contact=contact == "1")
    except fr.FeatureRequestError as e:
        form = {"title": title, "description": description, "category": category, "importance": importance,
                "contact": contact == "1"}
        return _page(request, db, sess, 400, error=str(e), form=form)
    return local_redirect("/feature-request?sent=1", status_code=303)
