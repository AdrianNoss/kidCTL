"""Verwaltungsbereich des Betreibers (Etappe 4.2): eigene Accounts und Sitzungen, IP-Beschränkung, Einladungscodes,
Übersicht der Familien.

Alles läuft im Plattformkontext und liest nur unverschlüsselte Spalten (Zahlen, Zeitstempel, Versionen) – ohne
Familienkontext gibt es keinen Schlüssel, Namen und Inhalte bleiben unlesbar.
"""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_platform_db
from .models import (
    Child, Device, Family, Membership, Operator, OperatorSession, ParentSession, SignupCode, utcnow,
)
from .security import PAIRING_ALPHABET, _check_origin, _form_token, client_ip, new_token, token_hash

COOKIE = "kidctl_admin"
SESSION_HOURS = 12
CODE_DAYS = 30


# ---------- Zugang ----------

def ip_allowed(ip: str) -> bool:
    if not settings.admin_allow_nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in settings.admin_allow_nets)


def check_network(request: Request) -> None:
    """Außerhalb von ADMIN_ALLOW_NETS gibt es den Verwaltungsbereich nicht (404, keine Auskunft)."""
    if not ip_allowed(client_ip(request)):
        raise HTTPException(404)


def create_session(db: Session, op: Operator) -> str:
    raw = new_token()
    db.add(OperatorSession(operator_id=op.id, token_hash=token_hash(raw), csrf_token=secrets.token_urlsafe(32),
                           expires_at=utcnow() + timedelta(hours=SESSION_HOURS)))
    db.execute(delete(OperatorSession).where(OperatorSession.expires_at < utcnow()))
    db.commit()
    return raw


def get_session(db: Session, request: Request) -> OperatorSession | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    sess = db.scalar(select(OperatorSession).where(OperatorSession.token_hash == token_hash(raw)))
    return sess if sess is not None and sess.expires_at > utcnow() else None


def require_operator(request: Request, db: Session = Depends(get_platform_db)) -> OperatorSession:
    check_network(request)
    sess = get_session(db, request)
    if sess is None:
        raise HTTPException(303, headers={"Location": "/admin/login"})
    request.state.operator_session = sess
    return sess


async def operator_csrf(request: Request, sess: OperatorSession = Depends(require_operator)) -> OperatorSession:
    import hmac
    _check_origin(request)
    if not hmac.compare_digest(await _form_token(request), sess.csrf_token):
        raise HTTPException(403, "CSRF-Token ungültig")
    return sess


# ---------- Einladungscodes ----------

def normalize_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def create_code(db: Session, note: str, operator_id: int | None) -> str:
    """Neuer Code (12 Zeichen, gut abtippbar, angezeigt als XXXX-XXXX-XXXX); gespeichert wird nur der Hash."""
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(12))
    db.add(SignupCode(code_hash=token_hash(raw), note=note.strip()[:255], created_by=operator_id,
                      expires_at=utcnow() + timedelta(days=CODE_DAYS)))
    db.commit()
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def find_code(db: Session, code: str) -> SignupCode | None:
    """Gültiger, noch nicht eingelöster Code (für die Registrierung, Etappe 4.3)."""
    c = db.scalar(select(SignupCode).where(SignupCode.code_hash == token_hash(normalize_code(code))))
    return c if c is not None and c.used_at is None and c.expires_at > utcnow() else None


# ---------- Übersicht ----------

@dataclass
class FamilyRow:
    id: int
    created_at: datetime
    paused_at: datetime | None
    parents: int = 0
    children: int = 0
    devices: int = 0
    offline: int = 0                # aktive Geräte, die sich seit über 48 h nicht gemeldet haben
    unsupported: int = 0            # Agent mit nicht mehr unterstützter Protokollversion
    last_seen: datetime | None = None
    versions: list[str] = field(default_factory=list)
    last_login: datetime | None = None   # letzte Anmeldung eines Elternteils


def families(db: Session) -> list[FamilyRow]:
    rows = {f.id: FamilyRow(f.id, f.created_at, f.paused_at)
            for f in db.execute(select(Family.id, Family.created_at, Family.paused_at).order_by(Family.id))}

    def count(model, *where):
        return db.execute(select(model.family_id, func.count()).where(*where).group_by(model.family_id))

    for fid, n in count(Membership):
        rows[fid].parents = n
    for fid, n in count(Child):
        rows[fid].children = n
    active = Device.revoked_at.is_(None)
    for fid, n in count(Device, active):
        rows[fid].devices = n
    for fid, n in count(Device, active, Device.last_seen_at < utcnow() - timedelta(hours=48)):
        rows[fid].offline = n
    for fid, n in count(Device, active, Device.protocol_unsupported.is_(True)):
        rows[fid].unsupported = n
    for fid, seen in db.execute(select(Device.family_id, func.max(Device.last_seen_at)).group_by(Device.family_id)):
        rows[fid].last_seen = seen
    for fid, version in db.execute(select(Device.family_id, Device.agent_version).where(active, Device.agent_version != "")
                                   .distinct().order_by(Device.family_id, Device.agent_version)):
        rows[fid].versions.append(version)
    for fid, at in db.execute(select(Membership.family_id, func.max(ParentSession.created_at))
                              .join(ParentSession, ParentSession.parent_id == Membership.parent_id)
                              .group_by(Membership.family_id)):
        rows[fid].last_login = at
    return list(rows.values())
