"""Mandantentrennung: Familienkontext je Datenbanksitzung, Pflicht-Filterung, Schreibschutz.

Jede Sitzung ist in genau einem Zustand:

- **Familie** (``set_family``): Abfragen auf familienbezogene Tabellen (``FamilyScoped``) und auf Accounts samt ihren
  Sitzungen, Tokens und Push-Abos werden automatisch auf diese Familie gefiltert (``with_loader_criteria``). Neue
  Zeilen bekommen die Familie automatisch; Zeilen einer anderen Familie zu schreiben schlägt fehl.
- **Plattform** (``set_platform`` / ``platform()``): keine Filterung – nur für Anmeldung, Token-Prüfung der Geräte,
  Hintergrundaufgaben über alle Familien und die Verwaltung. Neue familienbezogene Zeilen brauchen dann eine
  ausdrücklich gesetzte ``family_id``.
- **ohne Kontext**: Abfragen auf geschützte Tabellen schlagen mit ``TenantError`` fehl. So fällt ein vergessener
  Kontext sofort auf, statt still fremde Daten zu zeigen.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from . import crypto
from .db import SessionLocal
from .models import Family, FamilyScoped, Membership, Parent, ParentSession, ParentToken, PushSubscription

FAMILY, PLATFORM = "kidctl_family_id", "kidctl_platform"
PARENT_LINKED = (ParentSession, ParentToken, PushSubscription)   # gehören über parent_id zu einer Familie
PROTECTED = (Parent, Family, *PARENT_LINKED)


class TenantError(RuntimeError):
    """Datenbankzugriff ohne bzw. mit falschem Familienkontext."""


def set_family(db: Session, family_id: int) -> None:
    if not family_id:
        raise TenantError("Familienkontext ohne Familie")
    db.info.pop(PLATFORM, None)
    db.info[FAMILY] = int(family_id)


def set_platform(db: Session) -> None:
    db.info.pop(FAMILY, None)
    db.info[PLATFORM] = True


def family_of(db: Session) -> int | None:
    return db.info.get(FAMILY)


def is_platform(db: Session) -> bool:
    return bool(db.info.get(PLATFORM))


@contextmanager
def platform(db: Session) -> Iterator[Session]:
    """Vorübergehend ohne Filterung (z. B. Anmeldung, Token-Prüfung); danach wieder der vorherige Kontext."""
    saved = {k: db.info[k] for k in (FAMILY, PLATFORM) if k in db.info}
    set_platform(db)
    try:
        yield db
    finally:
        db.info.pop(PLATFORM, None)
        db.info.update(saved)


def family_session(family_id: int) -> Session:
    db = SessionLocal()
    set_family(db, family_id)
    return db


def platform_session() -> Session:
    db = SessionLocal()
    set_platform(db)
    return db


def family_ids(db: Session) -> list[int]:
    with platform(db):
        return list(db.scalars(select(Family.id).order_by(Family.id)))


def family_of_parent(db: Session, parent_id: int) -> int | None:
    with platform(db):
        return db.scalar(select(Membership.family_id).where(Membership.parent_id == parent_id))


def load_parent(db: Session, parent_id: int) -> Parent | None:
    """Für Abläufe ohne Anmeldung (Login, Links aus Mails): in den Kontext der Familie des Accounts wechseln und ihn
    laden – seine E-Mail ist mit dem Schlüssel dieser Familie verschlüsselt."""
    family_id = family_of_parent(db, parent_id)
    if family_id is None:
        return None
    set_family(db, family_id)
    return db.get(Parent, parent_id)


def _protected(state: ORMExecuteState) -> bool:
    return any(issubclass(m.class_, FamilyScoped) or m.class_ in PROTECTED for m in state.all_mappers)


@event.listens_for(Session, "do_orm_execute")
def _scope(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    db = state.session
    crypto.set_current(db.info.get(FAMILY))   # Schlüssel für Bind-Parameter und Ergebnisse dieser Abfrage
    if db.info.get(PLATFORM):
        return
    fid = db.info.get(FAMILY)
    if fid is None:
        if _protected(state):
            names = ", ".join(sorted({m.class_.__name__ for m in state.all_mappers}))
            raise TenantError(f"Datenbankzugriff ohne Familienkontext ({names})")
        return
    members = select(Membership.parent_id).where(Membership.family_id == fid).scalar_subquery()
    state.statement = state.statement.options(
        with_loader_criteria(FamilyScoped, lambda cls: cls.family_id == fid, include_aliases=True),
        with_loader_criteria(Family, Family.id == fid, include_aliases=True),
        with_loader_criteria(Parent, Parent.id.in_(members), include_aliases=True),
        *(with_loader_criteria(c, c.parent_id.in_(members), include_aliases=True) for c in PARENT_LINKED),
    )


@event.listens_for(Family, "after_insert")
def _family_key(_mapper, connection, target: Family) -> None:
    """Jede neue Familie bekommt sofort ihren eigenen Datenschlüssel."""
    import os
    from sqlalchemy import update
    if target.wrapped_key is None:
        dek = os.urandom(64)
        wrapped = crypto.wrap_family_key(target.id, dek)
        connection.execute(update(Family.__table__).where(Family.__table__.c.id == target.id).values(wrapped_key=wrapped))
        target.wrapped_key = wrapped
        crypto.remember(target.id, dek)


@event.listens_for(Session, "before_flush")
def _assign(db: Session, _ctx, _instances) -> None:
    fid = db.info.get(FAMILY)
    crypto.set_current(fid)
    for obj in db.new:
        if not isinstance(obj, FamilyScoped):
            continue
        if obj.family_id is None:
            if fid is None:
                raise TenantError(f"{type(obj).__name__} ohne Familie (kein Familienkontext)")
            obj.family_id = fid
        elif fid is not None and obj.family_id != fid:
            raise TenantError(f"{type(obj).__name__} gehört zu einer anderen Familie")
    if fid is not None:
        for obj in db.dirty:
            if isinstance(obj, FamilyScoped) and obj.family_id != fid:
                raise TenantError(f"{type(obj).__name__} gehört zu einer anderen Familie")
