"""Eltern-WebUI: Übersicht, Kinder, Geräte, Eltern-Accounts."""

from __future__ import annotations

from datetime import time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kidctl_common.protocol import CLOSE_REVOKED, SUPPORTED_VERSIONS

from ..config import settings
from ..db import get_db
from ..models import Child, Device, DeviceUser, PairingCode, Parent, ParentSession, WeekdayRule, utcnow
from ..security import (
    csrf_protect, hash_password, new_pairing_code, new_totp_secret, require_session, token_hash,
    totp_qr_svg, totp_uri,
)
from ..web import render
from ..ws import manager

router = APIRouter(dependencies=[Depends(require_session)])


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404)
    return obj


# ---------- Übersicht ----------

@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    children = db.scalars(select(Child).order_by(Child.name)).all()
    devices = db.scalars(select(Device).where(Device.revoked_at.is_(None)).order_by(Device.name)).all()
    return render(request, "dashboard.html", children=children, devices=devices, online=manager.is_online)


# ---------- Kinder ----------

@router.get("/children")
def children_list(request: Request, db: Session = Depends(get_db)):
    children = db.scalars(select(Child).order_by(Child.name)).all()
    return render(request, "children.html", children=children)


@router.post("/children", dependencies=[Depends(csrf_protect)])
def child_create(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return _redirect("/children")
    child = Child(name=name, rules=[
        WeekdayRule(weekday=d, quota_minutes=60, window_start=time(8, 0), window_end=time(20, 0))
        for d in range(7)
    ])
    db.add(child)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        children = db.scalars(select(Child).order_by(Child.name)).all()
        return render(request, "children.html", status_code=400, children=children,
                      error=f"Ein Kind namens „{name}“ existiert bereits.")
    return _redirect("/children")


@router.post("/children/{child_id}/delete", dependencies=[Depends(csrf_protect)])
def child_delete(child_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Child, child_id))
    db.commit()
    return _redirect("/children")


# ---------- Geräte ----------

@router.get("/devices")
def devices_list(request: Request, db: Session = Depends(get_db)):
    devices = db.scalars(select(Device).order_by(Device.revoked_at.is_not(None), Device.name)).all()
    return render(request, "devices.html", devices=devices, online=manager.is_online)


@router.post("/devices/pair")
def device_pair(request: Request, name: str = Form(...),
                sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    name = name.strip() or "Neues Gerät"
    code = new_pairing_code()
    pc = PairingCode(code_hash=token_hash(code), device_name=name[:128], created_by=sess.parent_id,
                     expires_at=utcnow() + timedelta(minutes=settings.pairing_code_minutes))
    db.add(pc)
    db.commit()
    return render(request, "pair.html", code=code, name=name, expires_at=pc.expires_at,
                  base_url=settings.base_url, minutes=settings.pairing_code_minutes)


@router.get("/devices/{device_id}")
def device_detail(device_id: int, request: Request, db: Session = Depends(get_db)):
    device = _get_or_404(db, Device, device_id)
    children = db.scalars(select(Child).order_by(Child.name)).all()
    return render(request, "device.html", device=device, children=children,
                  online=manager.is_online(device.id), supported=sorted(SUPPORTED_VERSIONS))


@router.post("/devices/{device_id}/mapping", dependencies=[Depends(csrf_protect)])
async def device_mapping(device_id: int, request: Request, db: Session = Depends(get_db)):
    device = _get_or_404(db, Device, device_id)
    form = await request.form()
    child_ids = set(db.scalars(select(Child.id)))
    for du in device.users:
        raw = str(form.get(f"user_{du.id}", ""))
        du.child_id = int(raw) if raw.isdigit() and int(raw) in child_ids else None
    db.commit()
    return _redirect(f"/devices/{device_id}")


@router.post("/devices/{device_id}/alert", dependencies=[Depends(csrf_protect)])
def device_alert(device_id: int, hours: int = Form(...), db: Session = Depends(get_db)):
    device = _get_or_404(db, Device, device_id)
    device.alert_after_hours = max(1, min(hours, 24 * 30))
    db.commit()
    return _redirect(f"/devices/{device_id}")


@router.post("/devices/{device_id}/revoke", dependencies=[Depends(csrf_protect)])
async def device_revoke(device_id: int, db: Session = Depends(get_db)):
    device = _get_or_404(db, Device, device_id)
    if device.revoked_at is None:
        device.revoked_at = utcnow()
        db.commit()
    await manager.disconnect(device_id, CLOSE_REVOKED, "Token widerrufen")
    return _redirect(f"/devices/{device_id}")


# ---------- Eltern-Accounts ----------

@router.get("/parents")
def parents_list(request: Request, db: Session = Depends(get_db)):
    parents = db.scalars(select(Parent).order_by(Parent.username)).all()
    return render(request, "parents.html", parents=parents)


@router.post("/parents", dependencies=[Depends(csrf_protect)])
def parent_create(request: Request, username: str = Form(...), display_name: str = Form(""),
                  password: str = Form(...), db: Session = Depends(get_db)):
    username = username.strip()
    error = None
    if not username or len(username) > 64:
        error = "Ungültiger Benutzername."
    elif len(password) < 12:
        error = "Passwort muss mindestens 12 Zeichen haben."
    elif len(password.encode()) > 72:
        error = "Passwort darf höchstens 72 Bytes haben."
    elif db.scalar(select(Parent).where(Parent.username == username)):
        error = "Benutzername existiert bereits."
    if error:
        parents = db.scalars(select(Parent).order_by(Parent.username)).all()
        return render(request, "parents.html", status_code=400, parents=parents, error=error)

    parent = Parent(username=username, display_name=display_name.strip()[:128],
                    password_hash=hash_password(password), totp_secret=new_totp_secret())
    db.add(parent)
    db.commit()
    uri = totp_uri(parent)
    return render(request, "parent_created.html", parent=parent, uri=uri, qr_svg=totp_qr_svg(uri),
                  secret=parent.totp_secret)


@router.post("/parents/{parent_id}/delete")
def parent_delete(parent_id: int, sess: ParentSession = Depends(csrf_protect),
                  db: Session = Depends(get_db)):
    if parent_id == sess.parent_id:
        raise HTTPException(400, "Eigenen Account nicht löschbar")
    if db.scalar(select(func.count()).select_from(Parent)) <= 1:
        raise HTTPException(400, "Letzter Account nicht löschbar")
    db.delete(_get_or_404(db, Parent, parent_id))
    db.commit()
    return _redirect("/parents")
