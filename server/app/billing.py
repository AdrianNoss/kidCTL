"""Abo-Vorbereitung (Etappe 5, nur SaaS): Tarif, Status, Testphase, Schonfrist, Ruhemodus, Löschung.

Zeitachse einer Familie mit Ablauf (Testphase oder bezahlte Laufzeit):

    … aktiv … | Ablauf | 14 Tage Schonfrist (Hinweise, Banner) | Ruhemodus (paused_at) | 90 Tage | Löschung

- **Ruhemodus:** Agenten bekommen eine Policy ohne Zuordnungen (Kinder nie ohne ansprechbare Eltern eingeschränkt),
  das WebUI zeigt nur noch Einstellungen (Abo, Export, Löschen), Profil und Abmelden. Sobald wieder bezahlt ist
  (Verwaltungsbereich oder Webhook), endet er sofort.
- **Tarife:** ``free`` (kostenlos, ohne Ablauf) und ``standard``; Kindergrenze je Tarif (vorerst keine).
- **Einzelbetrieb:** keine Fristen, keine Grenzen.
- Hinweise (Mail an die Inhaber, Push an alle Eltern) werden in ``family.billing_notified`` vermerkt, damit jeder nur
  einmal kommt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import i18n, mail
from .config import settings
from .i18n import N_, _
from .models import ROLE_OWNER, Child, Family, Membership, Parent, utcnow

log = logging.getLogger("kidctl.billing")

PLANS = {"free": N_("Kostenlos"), "standard": N_("Standard")}
MAX_CHILDREN: dict[str, int | None] = {"free": None, "standard": None}   # Stufen kommen mit den Preisen
STATUSES = {"trial": N_("Testphase"), "active": N_("aktiv"), "canceled": N_("gekündigt"), "past_due": N_("überfällig")}
AUDIT_FIELDS = ["plan", "billing_status", "trial_ends_at", "paid_until", "billing_provider", "billing_customer"]
GRACE_DAYS = 14
DELETE_DAYS = 90
GRACE_WARN = (14, 7, 1)     # Tage bis zum Ruhemodus
DELETE_WARN = (30, 7, 1)    # Tage bis zur Löschung
CHECK_INTERVAL = 3600


@dataclass
class State:
    phase: str                     # ok | grace | paused
    until: datetime | None         # Ende von Testphase/Laufzeit (None = ohne Ende)
    grace_end: datetime | None     # Beginn des Ruhemodus
    delete_at: datetime | None     # Löschung (nur im Ruhemodus)


def access_until(f: Family) -> datetime | None:
    if not settings.saas or f.plan == "free":
        return None
    if f.billing_status == "trial":
        return f.trial_ends_at
    return f.paid_until   # aktiv ohne Datum = ohne Ende; gekündigt/überfällig bekommen beim Setzen ein Datum


def state(f: Family, now: datetime | None = None) -> State:
    now = now or utcnow()
    until = access_until(f)
    if until is None or now < until:
        return State("ok", until, None, None)
    grace_end = until + timedelta(days=GRACE_DAYS)
    if now < grace_end and f.paused_at is None:   # einmal pausiert bleibt pausiert, bis wieder bezahlt ist
        return State("grace", until, grace_end, None)
    paused = f.paused_at or now
    return State("paused", until, grace_end, max(paused, grace_end) + timedelta(days=DELETE_DAYS))


def start_trial(f: Family) -> None:
    f.plan, f.billing_status = "standard", "trial"
    f.trial_ends_at = utcnow() + timedelta(days=settings.trial_days)


def set_status(f: Family, status: str, paid_until: datetime | None = None) -> None:
    """Status setzen (Verwaltungsbereich, Webhook). Gekündigt/überfällig ohne Datum: ab sofort abgelaufen."""
    if status not in STATUSES:
        raise ValueError(status)
    f.billing_status = status
    if paid_until is not None:
        f.paid_until = paid_until
    elif status in ("canceled", "past_due") and (f.paid_until is None or f.paid_until > utcnow()):
        f.paid_until = f.paid_until or utcnow()


def child_limit_error(db: Session) -> str | None:
    """Grenzprüfung beim Anlegen eines Kindes (Familienkontext)."""
    if not settings.saas:
        return None
    f = db.scalar(select(Family))
    limit = MAX_CHILDREN.get(f.plan) if f else None
    if limit is not None and (db.scalar(select(Child.id).offset(limit - 1).limit(1)) is not None):
        return _("Dein Tarif umfasst höchstens {n} Kinder.").format(n=limit)
    return None


def address_needed(f: Family, address) -> bool:
    """Rechnungsangaben fehlen, werden aber gebraucht: bezahlter Tarif außerhalb der Testphase oder Testphase in den
    letzten 7 Tagen bzw. abgelaufen."""
    if not settings.saas or f.plan == "free" or (address is not None and address.complete):
        return False
    if f.billing_status != "trial":
        return True
    return f.trial_ends_at is not None and f.trial_ends_at - utcnow() < timedelta(days=7)


def banner(f: Family, address=None) -> dict | None:
    """Hinweis oben im WebUI (Schonfrist, Ruhemodus, Rechnungsangaben fehlen) oder ``None``."""
    st = state(f)
    if st.phase == "grace":
        return {"kind": "grace", "date": st.grace_end}
    if st.phase == "paused":
        return {"kind": "paused", "date": st.delete_at}
    if st.until is not None and f.billing_status == "trial" and st.until - utcnow() < timedelta(days=7):
        return {"kind": "trial", "date": st.until, "address": address_needed(f, address)}
    if address_needed(f, address):
        return {"kind": "address", "date": None}
    return None


# ---------- Hintergrundprüfung ----------

def _sent(f: Family) -> set[str]:
    return set(filter(None, f.billing_notified.split(",")))


def _mark(f: Family, key: str) -> None:
    keys = sorted(_sent(f) | {key})
    while len(",".join(keys)) > 255:
        keys.pop(0)
    f.billing_notified = ",".join(keys)


def _notify(family_id: int, subject: str, text: str, **fmt) -> None:
    """Mail an die Inhaber (Sprache des Accounts), Push an alle Eltern. Texte mit ``N_`` markiert."""
    from .push import send_to_parents_sync
    from .tenant import family_session
    with family_session(family_id) as db:
        owners = db.scalars(select(Parent).join(Membership, Membership.parent_id == Parent.id)
                            .where(Membership.role == ROLE_OWNER)).all()
        for p in owners:
            if p.email:
                from . import mailer
                lang = i18n.parent_language(p)
                with i18n.use(lang):
                    title = _(subject)
                    body = _(text).format(**{k: (i18n.fmt_date(v.date()) if isinstance(v, datetime) else v)
                                             for k, v in fmt.items()})
                mailer.send(p.email, "billing_notice", lang, name=p.display_name or p.username, title=title,
                            text=body)
    try:
        def title():
            return _(subject)

        def body():
            return _(text).format(**{k: (i18n.fmt_date(v.date()) if isinstance(v, datetime) else v)
                                     for k, v in fmt.items()})
        send_to_parents_sync(title, body, "/settings#abo", tag="billing", family_id=family_id)
    except Exception:
        log.exception("Push zum Abo an Familie %s fehlgeschlagen", family_id)


def check_family(db: Session, family_id: int, now: datetime | None = None) -> str:
    """Eine Familie prüfen (Plattformkontext). Rückgabe: ``""``, ``"paused"``/``"resumed"`` (Policies neu senden)
    oder ``"deleted"``."""
    now = now or utcnow()
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        return ""
    st = state(f, now)
    result = ""
    notes: list[tuple[str, str, dict]] = []
    from . import audit
    if st.phase != "paused" and f.paused_at is not None:
        audit.record(db, audit.SYSTEM, "subscription", "paused_at", f.paused_at, None, family_id=family_id)
        f.paused_at, f.billing_notified, result = None, "", "resumed"
        notes.append((N_("Abo aktiv"), N_("Danke! KidCTL ist wieder aktiv, die Regeln gelten ab sofort wieder."), {}))
    elif st.phase == "grace":
        days = (st.grace_end - now).days + 1
        for n in GRACE_WARN:
            key = f"grace:{st.until:%Y%m%d}:{n}"
            if days <= n and key not in _sent(f):
                for m in GRACE_WARN:           # ältere Stufen gelten mit als erledigt
                    if m >= n:
                        _mark(f, f"grace:{st.until:%Y%m%d}:{m}")
                notes.append((N_("Abo abgelaufen"), N_("Eure Testphase bzw. euer Abo ist abgelaufen. Ab {date} ist "
                              "KidCTL pausiert: Die Computer heben dann alle Einschränkungen auf."), {"date": st.grace_end}))
                break
    elif st.phase == "paused":
        if f.paused_at is None:
            audit.record(db, audit.SYSTEM, "subscription", "paused_at", None, now, family_id=family_id)
            f.paused_at, result = now, "paused"
            st = state(f, now)
            notes.append((N_("KidCTL pausiert"), N_("KidCTL ist pausiert: Die Computer eurer Kinder heben alle "
                          "Einschränkungen auf. Mit einem Abo gelten die Regeln sofort wieder. Ohne Abo werden eure "
                          "Daten am {date} gelöscht."), {"date": st.delete_at}))
        elif now >= st.delete_at:
            from .tenant import delete_family
            audit.record(db, audit.SYSTEM, "family", "gelöscht", f"{DELETE_DAYS} Tage Ruhemodus", None,
                         family_id=family_id)
            db.commit()
            counts = delete_family(db, family_id)
            log.warning("Familie %s nach %d Tagen Ruhemodus gelöscht: %s", family_id, DELETE_DAYS, counts)
            return "deleted"
        else:
            days = (st.delete_at - now).days + 1
            for n in DELETE_WARN:
                key = f"delete:{f.paused_at:%Y%m%d}:{n}"
                if days <= n and key not in _sent(f):
                    for m in DELETE_WARN:
                        if m >= n:
                            _mark(f, f"delete:{f.paused_at:%Y%m%d}:{m}")
                    notes.append((N_("Löschung eurer Daten"), N_("Ohne Abo werden eure KidCTL-Daten am {date} "
                                  "endgültig gelöscht. Vorher könnt ihr sie unter Einstellungen exportieren."),
                                  {"date": st.delete_at}))
                    break
    db.commit()
    for subject, text, fmt in notes:
        try:
            _notify(family_id, subject, text, **fmt)
        except Exception:
            log.exception("Hinweis zum Abo an Familie %s fehlgeschlagen", family_id)
    if result:
        log.info("Familie %s: %s", family_id, result)
    return result


def check_all(db: Session, now: datetime | None = None) -> dict[int, str]:
    if not settings.saas:
        return {}
    out = {}
    for fid in list(db.scalars(select(Family.id).where(Family.wrapped_key.is_not(None)))):
        try:
            if r := check_family(db, fid, now):
                out[fid] = r
        except Exception:
            db.rollback()
            log.exception("Abo-Prüfung für Familie %s fehlgeschlagen", fid)
    return out


async def push_changed(results: dict[int, str]) -> None:
    """Nach Wechsel in/aus dem Ruhemodus allen aktiven Geräten der Familie die Policy neu senden."""
    from .models import Device
    from .policy import push_policies
    from .tenant import family_session
    for fid, r in results.items():
        if r in ("paused", "resumed"):
            with family_session(fid) as db:
                await push_policies(db, set(db.scalars(select(Device.id).where(Device.revoked_at.is_(None)))))


async def apply(family_id: int) -> str:
    """Sofort prüfen (nach Änderung im Verwaltungsbereich oder per Webhook) und Policies senden."""
    import asyncio
    from .tenant import platform_session

    def run():
        with platform_session() as db:
            return check_family(db, family_id)
    r = await asyncio.to_thread(run)
    await push_changed({family_id: r})
    return r


async def run_forever() -> None:
    import asyncio
    from .tenant import platform_session
    while True:
        try:
            def run():
                with platform_session() as db:
                    return check_all(db)
            await push_changed(await asyncio.to_thread(run))
        except Exception:
            log.exception("Abo-Prüfung fehlgeschlagen")
        await asyncio.sleep(CHECK_INTERVAL)
