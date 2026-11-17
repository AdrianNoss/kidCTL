"""Support-Freigabe (Etappe 4.4): Eltern geben dem Betreiber zeitlich begrenzt Einsicht, nur lesend, protokolliert.

- Der Inhaber erteilt die Freigabe (1, 24 oder 72 Stunden); jedes Elternteil kann sie widerrufen.
- Der Betreiber öffnet aus ``/admin`` eine Support-Ansicht: eigener Cookie ``kidctl_support`` für die normalen Seiten.
  Sie gilt nur, solange Freigabe und Betreiber-Sitzung gelten (höchstens ``VIEW_HOURS``).
- Nur lesend: alle Aktionen (POST, CSRF-geschützt) und das Profil sind gesperrt.
- Jeder Seitenaufruf wird mit Betreiber und Pfad protokolliert; das Protokoll sehen alle Eltern der Familie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import (
    ROLE_OWNER, Membership, OperatorSession, Parent, SupportAccess, SupportGrant, SupportSession, utcnow,
)
from .security import new_token, token_hash
from .tenant import platform, set_family

COOKIE = "kidctl_support"
DURATIONS = (1, 24, 72)   # Stunden zur Auswahl
VIEW_HOURS = 2            # eine geöffnete Ansicht gilt höchstens so lange
QUIET = ("/chat/messages",)   # Live-Aktualisierung: nicht jeden Abruf protokollieren
BLOCKED = ("/profile",)       # persönlicher Bereich eines Elternteils


@dataclass
class SupportView:
    """Steht in der Support-Ansicht an Stelle der Eltern-Sitzung (``require_session``)."""
    id: None
    parent_id: int | None
    parent: Parent | None
    csrf_token: str
    family_id: int
    operator: str
    expires_at: object
    support: bool = True


# ---------- Familie ----------

def active_grant(db: Session) -> SupportGrant | None:
    """Aktuelle Freigabe der Familie im Kontext."""
    return db.scalar(select(SupportGrant).where(SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > utcnow())
                     .order_by(SupportGrant.expires_at.desc()))


def grant(db: Session, parent_id: int, hours: int) -> SupportGrant:
    revoke(db)
    g = SupportGrant(granted_by=parent_id, expires_at=utcnow() + timedelta(hours=hours))
    db.add(g)
    db.commit()
    return g


def revoke(db: Session) -> None:
    """Alle Freigaben der Familie beenden – offene Support-Ansichten enden mit (Prüfung bei jedem Aufruf)."""
    db.execute(update(SupportGrant).where(SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > utcnow())
               .values(revoked_at=utcnow()))
    db.commit()


def log(db: Session, limit: int = 100) -> list[SupportAccess]:
    return list(db.scalars(select(SupportAccess).order_by(SupportAccess.at.desc()).limit(limit)))


# ---------- Betreiber ----------

def granted_families(db: Session) -> dict[int, SupportGrant]:
    """Plattformkontext: Familien mit gültiger Freigabe (nur unverschlüsselte Spalten)."""
    rows = db.execute(select(SupportGrant.family_id, SupportGrant.id, SupportGrant.expires_at)
                      .where(SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > utcnow()))
    return {fid: (gid, exp) for fid, gid, exp in rows}


def open_view(db: Session, operator_session: OperatorSession, family_id: int) -> str | None:
    found = granted_families(db).get(family_id)
    if found is None:
        return None
    grant_id, grant_expires = found
    raw = new_token()
    expires = min(grant_expires, utcnow() + timedelta(hours=VIEW_HOURS), operator_session.expires_at)
    db.add(SupportSession(token_hash=token_hash(raw), operator_session_id=operator_session.id, grant_id=grant_id,
                          family_id=family_id, expires_at=expires))
    db.add(SupportAccess(family_id=family_id, operator=operator_session.operator.username, path="(geöffnet)"))
    db.commit()
    return raw


def close_view(db: Session, request: Request) -> None:
    raw = request.cookies.get(COOKIE)
    if raw:
        db.query(SupportSession).filter(SupportSession.token_hash == token_hash(raw)).delete()
        db.commit()


def view_for(db: Session, request: Request) -> SupportView | None:
    """Gültige Support-Ansicht zum Cookie (Plattformkontext): Ansicht, Freigabe und Betreiber-Sitzung müssen gelten."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    now = utcnow()
    row = db.execute(
        select(SupportSession.family_id, SupportSession.expires_at, OperatorSession.operator_id)
        .join(SupportGrant, SupportGrant.id == SupportSession.grant_id)
        .join(OperatorSession, OperatorSession.id == SupportSession.operator_session_id)
        .where(SupportSession.token_hash == token_hash(raw), SupportSession.expires_at > now,
               SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > now, OperatorSession.expires_at > now)
    ).first()
    if row is None:
        return None
    from .models import Operator
    name = db.scalar(select(Operator.username).where(Operator.id == row.operator_id)) or ""
    owner = db.scalar(select(Membership.parent_id).where(Membership.family_id == row.family_id,
                                                         Membership.role == ROLE_OWNER))
    return SupportView(id=None, parent_id=owner, parent=None, csrf_token="", family_id=row.family_id,
                       operator=name, expires_at=row.expires_at)


def enter(db: Session, request: Request, view: SupportView) -> None:
    """Im Familienkontext: Seite prüfen, protokollieren, Anzeige-Account laden."""
    path = request.url.path
    if path.startswith(BLOCKED):
        raise HTTPException(403, "Support-Ansicht: nicht verfügbar")
    set_family(db, view.family_id)
    view.parent = db.get(Parent, view.parent_id) if view.parent_id else None
    if request.method == "GET" and not path.endswith(QUIET):
        query = f"?{request.url.query}" if request.url.query else ""
        db.add(SupportAccess(operator=view.operator, path=(path + query)[:255]))
        db.commit()


def deny_write(sess) -> None:
    if getattr(sess, "support", False):
        raise HTTPException(403, "Support-Ansicht: nur lesend")


__all__ = ["COOKIE", "DURATIONS", "SupportView", "active_grant", "grant", "revoke", "log", "granted_families",
           "open_view", "close_view", "view_for", "enter", "deny_write", "platform"]
