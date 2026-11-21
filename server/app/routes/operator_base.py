"""Gemeinsames für die Router des Verwaltungsbereichs (/admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import operators as opr
from ..models import OperatorSession
from ..web import templates


def new_router() -> APIRouter:
    """Router unter /admin; nur aus erlaubten Netzen erreichbar (wie bisher für den ganzen Bereich)."""
    return APIRouter(prefix="/admin", dependencies=[Depends(opr.check_network)])


def render(request: Request, name: str, sess: OperatorSession, status_code: int = 200, **ctx):
    ctx.update(csrf_token=sess.csrf_token, current_parent=None, operator=sess.operator)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
