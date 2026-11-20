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

from . import versions
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


ROLES = {"admin": "Admin", "support": "Support"}


def require_admin(sess: OperatorSession = Depends(require_operator)) -> OperatorSession:
    """Nur Rolle *Admin*: Abo, Einladungscodes, Rechnungen, Familienname ändern."""
    if sess.operator.role != "admin":
        raise HTTPException(403, "Nur für Admins")
    return sess


async def admin_csrf(sess: OperatorSession = Depends(operator_csrf)) -> OperatorSession:
    if sess.operator.role != "admin":
        raise HTTPException(403, "Nur für Admins")
    return sess


# ---------- Einladungscodes ----------

def normalize_code(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


def create_code(db: Session, note: str, operator_id: int | None, plan: str = "", email: str | None = None,
                lang: str = "de") -> str:
    """Neuer Code (12 Zeichen, gut abtippbar, angezeigt als XXXX-XXXX-XXXX); gespeichert wird nur der Hash.
    Mit ``email`` bleibt die Adresse für „Erneut senden“ gespeichert (bis zum Einlösen bzw. Ablauf)."""
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(12))
    db.add(SignupCode(code_hash=token_hash(raw), note=note.strip()[:255], created_by=operator_id, plan=plan,
                      expires_at=utcnow() + timedelta(days=CODE_DAYS), email=email or None,
                      lang=lang if lang in ("de", "en") else "de"))
    db.commit()
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def send_invite(db: Session, code_row: SignupCode, code: str) -> bool:
    """Einladungsmail mit Code und Registrierungslink (Code eingesetzt) an die gespeicherte Adresse."""
    from urllib.parse import quote

    from . import mailer
    if not code_row.email:
        return False
    return mailer.send(code_row.email, "signup_invite", code_row.lang, code=code,
                       link=f"{settings.base_url}/signup?code={quote(code)}", expires=code_row.expires_at,
                       free=code_row.plan == "free", trial_days=settings.trial_days)


def mask_email(email: str | None) -> str:
    """m***@example.org – für die Liste der Codes."""
    if not email or "@" not in email:
        return ""
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def forget_invite_emails(db: Session) -> int:
    """Adressen eingelöster oder abgelaufener Codes löschen (Aufräumjob)."""
    from sqlalchemy import or_, update
    n = db.execute(update(SignupCode).where(SignupCode.email.is_not(None), or_(
        SignupCode.used_at.is_not(None), SignupCode.expires_at < utcnow())).values(email=None)).rowcount or 0
    db.commit()
    return n


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
    plan: str = "free"
    billing_status: str = "active"
    billing: object = None               # billing.State
    name: str = ""                       # Name der Familie (Plattform-Schlüssel)
    address_missing: bool = False        # Rechnungsangaben gebraucht, aber unvollständig
    oldest: str = ""                     # älteste Agent-Version der aktiven Geräte
    outdated: bool = False               # mindestens ein Agent hat ein Update offen bzw. ist zu alt
    support: bool = False                # Support-Freigabe aktiv
    tickets: int = 0                     # offene Tickets

    @property
    def activity(self) -> datetime | None:
        """Jüngere von Geräte- und Eltern-Aktivität."""
        return max((t for t in (self.last_seen, self.last_login) if t), default=None)

    @property
    def status(self) -> str:
        """trial | active | past_due | canceled | paused (für Filter und Sortierung)."""
        if self.paused_at:
            return "paused"
        return "active" if self.plan == "free" else self.billing_status

    @property
    def trial_ending(self) -> bool:
        st = self.billing
        return (self.status == "trial" and st is not None and st.until is not None
                and timedelta(0) <= st.until - utcnow() <= timedelta(days=7))


def open_tickets(db: Session) -> dict[int, int]:
    """Offene Tickets (alles außer „geschlossen“) je Familie."""
    from .models import Ticket
    rows = db.execute(select(Ticket.family_id, func.count()).where(Ticket.status != "closed")
                      .group_by(Ticket.family_id))
    return dict(rows.all())


def families(db: Session, only: int | None = None) -> list[FamilyRow]:
    """Metadaten aller Familien (bzw. nur ``only``) für Übersicht und Detailansicht."""
    from . import billing, support
    from .models import BillingAddress

    def scope(q, col):
        return q.where(col == only) if only is not None else q
    addresses = {a.family_id: a for a in db.scalars(scope(select(BillingAddress), BillingAddress.family_id))}
    rows = {f.id: FamilyRow(f.id, f.created_at, f.paused_at, plan=f.plan, billing_status=f.billing_status,
                            billing=billing.state(f), name=f.name,
                            address_missing=billing.address_needed(f, addresses.get(f.id)))
            for f in db.scalars(scope(select(Family).where(Family.wrapped_key.is_not(None)), Family.id).order_by(Family.id))}

    def count(model, *where):
        return db.execute(scope(select(model.family_id, func.count()).where(*where), model.family_id)
                          .group_by(model.family_id))

    for fid, n in count(Membership):
        if fid in rows:
            rows[fid].parents = n
    for fid, n in count(Child):
        if fid in rows:
            rows[fid].children = n
    active = Device.revoked_at.is_(None)
    stale = utcnow() - timedelta(hours=48)
    releases = {"linux": versions.latest(), "windows": versions.latest_windows()}
    for fid, version, plat, unsupported, seen in db.execute(scope(
            select(Device.family_id, Device.agent_version, Device.platform, Device.protocol_unsupported,
                   Device.last_seen_at).where(active), Device.family_id)):
        r = rows.get(fid)
        if r is None:
            continue
        r.devices += 1
        if seen is not None and seen < stale:
            r.offline += 1
        if unsupported:
            r.unsupported += 1
        if version:
            if version not in r.versions:
                r.versions.append(version)
            if not r.oldest or versions._key(version) < versions._key(r.oldest):
                r.oldest = version
        release = releases.get(plat)   # einmal je Anfrage geladen; ohne Repo nur „Protokoll zu alt“
        if unsupported or (release and version[:1].isdigit()
                           and versions._key(version) < versions._key(release.version)):
            r.outdated = True
    for r in rows.values():
        r.versions.sort(key=versions._key)
    for fid, seen in db.execute(scope(select(Device.family_id, func.max(Device.last_seen_at)), Device.family_id)
                                .group_by(Device.family_id)):
        if fid in rows:
            rows[fid].last_seen = seen
    for fid, at in db.execute(scope(select(Membership.family_id, func.max(ParentSession.created_at))
                                    .join(ParentSession, ParentSession.parent_id == Membership.parent_id),
                                    Membership.family_id).group_by(Membership.family_id)):
        if fid in rows:
            rows[fid].last_login = at
    for fid in support.granted_families(db):
        if fid in rows:
            rows[fid].support = True
    for fid, n in open_tickets(db).items():
        if fid in rows:
            rows[fid].tickets = n
    return list(rows.values())


# ---------- Filtern, Sortieren, Blättern (Übersicht) ----------

PAGE_SIZE = 50
STATUS_FILTERS = ("trial", "trial_ending", "active", "past_due", "canceled", "paused")
PLAN_FILTERS = ("free", "standard")
ISSUE_FILTERS = ("offline", "outdated", "address", "ticket", "support")
IDLE_FILTERS = ("7", "30", "90")       # Tage ohne Aktivität (Geräte oder Eltern); nie aktiv zählt mit
SORTS = {
    "id": lambda r: r.id,
    "name": lambda r: (r.name or "").casefold(),
    "since": lambda r: r.created_at,
    "children": lambda r: (r.children, r.devices),
    "agent": lambda r: (bool(r.oldest), versions._key(r.oldest)),
    "activity": lambda r: r.activity or datetime.min,
    "abo": lambda r: (r.plan, r.status, r.billing.until or datetime.max if r.billing else datetime.max),
    "state": lambda r: (r.status == "paused", r.outdated, r.offline, r.tickets),
}


def _issue(r: FamilyRow, issue: str) -> bool:
    return {"offline": r.offline > 0, "outdated": r.outdated, "address": r.address_missing,
            "ticket": r.tickets > 0, "support": r.support}[issue]


def parse_day(value: str):
    """Datum aus dem Filter (JJJJ-MM-TT); Ungültiges zählt als nicht gesetzt."""
    from datetime import date
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def select_rows(rows: list[FamilyRow], q: str = "", status: str = "", plan: str = "", issue: str = "",
                sort: str = "id", since: str = "", until: str = "", idle: str = "") -> list[FamilyRow]:
    q = q.strip().casefold()
    if q:
        num = q.lstrip("#")
        rows = [r for r in rows if (num.isdigit() and r.id == int(num)) or q in (r.name or "").casefold()]
    if status == "trial_ending":
        rows = [r for r in rows if r.trial_ending]
    elif status in STATUS_FILTERS:
        rows = [r for r in rows if r.status == status]
    if plan in PLAN_FILTERS:
        rows = [r for r in rows if r.plan == plan]
    if issue in ISSUE_FILTERS:
        rows = [r for r in rows if _issue(r, issue)]
    if (day := parse_day(since)):
        rows = [r for r in rows if r.created_at.date() >= day]
    if (day := parse_day(until)):
        rows = [r for r in rows if r.created_at.date() <= day]
    if idle in IDLE_FILTERS:
        cutoff = utcnow() - timedelta(days=int(idle))
        rows = [r for r in rows if not r.activity or r.activity < cutoff]
    key = sort.lstrip("-")
    return sorted(rows, key=SORTS.get(key, SORTS["id"]), reverse=sort.startswith("-") and key in SORTS)
