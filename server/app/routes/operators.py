"""Verwaltungsbereich des Betreibers unter /admin (Etappe 4.2) – getrennt von den Eltern-Seiten."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit, backup, billing, support, tickets
from ..config import settings
from ..db import get_platform_db
from ..i18n import N_, _
from ..models import Operator, OperatorSession, SignupCode, utcnow
from ..security import (
    client_ip, csrf_protect_anon, dummy_verify, is_rate_limited, record_failure, totp_locked,
    verify_password, verify_totp,
)
from ..web import local_redirect, render_anon, templates

router = APIRouter(prefix="/admin", dependencies=[Depends(opr.check_network)])


def _render(request: Request, name: str, sess: OperatorSession, status_code: int = 200, **ctx):
    ctx.update(csrf_token=sess.csrf_token, current_parent=None, operator=sess.operator)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# ---------- Anmeldung ----------

@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_platform_db)):
    if opr.get_session(db, request):
        return RedirectResponse("/admin", status_code=303)
    return render_anon(request, "admin_login.html")


@router.post("/login", dependencies=[Depends(csrf_protect_anon)])
def login(request: Request, username: str = Form(""), password: str = Form(""), totp: str = Form(""),
          db: Session = Depends(get_platform_db)):
    ip, username = client_ip(request), username.strip()
    key = f"op:{username}"[:64]   # eigener Zähler, unabhängig von gleichnamigen Eltern-Accounts
    if is_rate_limited(db, "admin", ip, key):
        return render_anon(request, "admin_login.html", status_code=429, username=username,
                           error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))
    op = db.scalar(select(Operator).where(Operator.username == username)) if username else None
    ok = False
    if op is None:
        dummy_verify()
    elif verify_password(password, op.password_hash):
        if totp_locked(db, key):
            return render_anon(request, "admin_login.html", status_code=429, username=username,
                               error=_("Zu viele Fehlversuche. Bitte später erneut versuchen."))
        ok = verify_totp(db, op, totp)
        if not ok:
            record_failure(db, "totp", ip, key)
    if not ok:
        record_failure(db, "admin", ip, key)
        return render_anon(request, "admin_login.html", status_code=401, username=username,
                           error=_("Anmeldung fehlgeschlagen."))
    db.commit()   # totp_last_step
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(opr.COOKIE, opr.create_session(db, op), max_age=opr.SESSION_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/admin")
    return resp


@router.post("/logout")
def logout(sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    db.execute(delete(OperatorSession).where(OperatorSession.id == sess.id))   # Support-Ansichten enden mit
    db.commit()
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(opr.COOKIE, path="/admin")
    resp.delete_cookie(support.COOKIE, path="/")
    return resp


# ---------- Übersicht ----------

@router.get("")
def overview(request: Request, q: str = "", status: str = "", plan: str = "", issue: str = "", since: str = "",
             until: str = "", idle: str = "", sort: str = "id", page: int = 1,
             sess: OperatorSession = Depends(opr.require_operator), db: Session = Depends(get_platform_db)):
    """Familien: Kacheln, Suche, Filter (einfach und „Erweitert“), Sortierung, 50 je Seite – alles im Server,
    Parameter in der URL."""
    from urllib.parse import urlencode
    since = since if opr.parse_day(since) else ""
    until = until if opr.parse_day(until) else ""
    params = {k: v for k, v in {"q": q.strip(), "status": status, "plan": plan, "issue": issue, "since": since,
                                "until": until, "idle": idle}.items() if v}

    def url(**change):
        merged = {**params, "sort": sort, "page": 1, **change}
        merged = {k: v for k, v in merged.items() if v not in ("", None) and not (k == "sort" and v == "id")
                  and not (k == "page" and v == 1)}
        return "/admin" + (f"?{urlencode(merged)}" if merged else "")

    # Das Suchformular schickt auch leere Felder mit – auf eine saubere Adresse umleiten (teilbar, lesbar)
    if any(not v for v in request.query_params.values()):
        return RedirectResponse(url(page=page), status_code=303)

    rows = opr.families(db)
    backups = backup.backups()
    tiles = {"families": len(rows), "children": sum(r.children for r in rows), "devices": sum(r.devices for r in rows),
             "trial": sum(1 for r in rows if r.status == "trial"),
             "trial_ending": sum(1 for r in rows if r.trial_ending),
             "paused": sum(1 for r in rows if r.status == "paused"),
             "tickets": sum(r.tickets for r in rows)}
    selected = opr.select_rows(rows, q, status, plan, issue, sort, since=since, until=until, idle=idle)
    pages = max(1, -(-len(selected) // opr.PAGE_SIZE))
    page = min(max(1, page), pages)

    def sort_url(key):
        return url(sort=f"-{key}" if sort == key else key)

    return _render(request, "admin_overview.html", sess, families=selected[(page - 1) * opr.PAGE_SIZE:page * opr.PAGE_SIZE],
                   total=len(selected), all_total=len(rows), tiles=tiles, page=page, pages=pages, url=url,
                   sort_url=sort_url, sort=sort, f={"q": q.strip(), "status": status, "plan": plan, "issue": issue,
                                                    "since": since, "until": until, "idle": idle},
                   parse_day=opr.parse_day, backups=backups, backup_error=backup.last_error(), plans=billing.PLANS, statuses=billing.STATUSES,
                   granted=support.granted_families(db))


# ---------- Abo einer Familie ----------

EDIT_CARDS = ("name", "abo")


def _family_page(request, db, sess, family_id, status_code=200, edit: str = "", **ctx):
    """Detailansicht: Karten Kopf, Abo, Rechnungsangaben, Nutzung, Support, Rechnungen, Verlauf (nur Metadaten außer
    Name und Rechnungsangaben). ``edit`` = Karte als Formular (nur Admin)."""
    from ..models import AdminLog, BillingAddress, Device, Family, SupportAccess, SupportGrant
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    rows = opr.families(db, only=family_id)
    devices = db.execute(select(Device.id, Device.platform, Device.agent_version, Device.last_seen_at,
                                Device.revoked_at, Device.protocol_unsupported)
                         .where(Device.family_id == family_id).order_by(Device.revoked_at.is_not(None), Device.id)).all()
    accesses = db.execute(select(SupportAccess.at, SupportAccess.operator, SupportAccess.path)
                          .where(SupportAccess.family_id == family_id)
                          .order_by(SupportAccess.at.desc()).limit(10)).all()
    last_grant = db.execute(select(SupportGrant.created_at, SupportGrant.expires_at, SupportGrant.revoked_at)
                            .where(SupportGrant.family_id == family_id)
                            .order_by(SupportGrant.created_at.desc()).limit(1)).first()
    history = db.scalars(select(AdminLog).where(AdminLog.family_id == family_id)
                         .order_by(AdminLog.at.desc(), AdminLog.id.desc()).limit(15)).all()
    from ..models import Ticket
    family_tickets = db.scalars(select(Ticket).where(Ticket.family_id == family_id)
                                .order_by(Ticket.status == "closed", Ticket.updated_at.desc()).limit(10)).all()
    is_admin = sess.operator.role == "admin"
    from ..models import Invoice, PriceItem
    family_invoices = db.scalars(select(Invoice).where(Invoice.family_id == family_id)
                                 .order_by(Invoice.created_at.desc())).all() if is_admin else []
    price_items = db.scalars(select(PriceItem).where(PriceItem.active.is_(True)).order_by(PriceItem.id)).all() \
        if is_admin else []
    return _render(request, "admin_family.html", sess, status_code, f=f, row=rows[0] if rows else None,
                   family_invoices=family_invoices, price_items=price_items, eur=invoices.eur,
                   invoice_statuses=invoices.STATUSES,
                   family_tickets=family_tickets, ticket_statuses=tickets.STATUSES,
                   st=billing.state(f), devices=devices, accesses=accesses, last_grant=last_grant, history=history,
                   address=db.scalar(select(BillingAddress).where(BillingAddress.family_id == family_id)),
                   granted=support.granted_families(db).get(family_id), is_admin=is_admin,
                   edit=edit if is_admin and edit in EDIT_CARDS else "", log_objects=LOG_OBJECTS,
                   plans=billing.PLANS, statuses=billing.STATUSES, **ctx)


@router.get("/families/{family_id}")
def family_detail(family_id: int, request: Request, edit: str = "",
                  sess: OperatorSession = Depends(opr.require_operator), db: Session = Depends(get_platform_db)):
    return _family_page(request, db, sess, family_id, edit=edit, saved=request.query_params.get("saved"))


def _date(value: str):
    from datetime import date, datetime, time
    value = value.strip()
    return datetime.combine(date.fromisoformat(value), time(23, 59)) if value else None


@router.post("/families/{family_id}/name")
def family_name(family_id: int, name: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    before = audit.snapshot(f, ["name"])
    f.name = name.strip()[:128]
    audit.changes(db, audit.actor_of(sess), "family", before, f, family_id=family_id)
    db.commit()
    return local_redirect(f"/admin/families/{family_id}?saved=1#kopf", status_code=303)


@router.post("/families/{family_id}")
async def family_billing(family_id: int, request: Request, plan: str = Form(...), status: str = Form(...),
                         trial_ends: str = Form(""), paid_until: str = Form(""), provider: str = Form(""),
                         customer: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                         db: Session = Depends(get_platform_db)):
    """Tarif und Abo-Status von Hand setzen (bis ein Zahlungsanbieter angebunden ist); wirkt sofort."""
    from ..models import Family
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    try:
        if plan not in billing.PLANS or status not in billing.STATUSES:
            raise ValueError
        trial, paid = _date(trial_ends), _date(paid_until)
    except ValueError:
        return _family_page(request, db, sess, family_id, 400, edit="abo", error=_("Ungültige Eingabe."))
    before = audit.snapshot(f, billing.AUDIT_FIELDS)
    f.plan, f.trial_ends_at, f.paid_until = plan, trial, paid
    f.billing_provider, f.billing_customer = provider.strip()[:32], customer.strip()[:128]
    billing.set_status(f, status, paid)
    audit.changes(db, audit.actor_of(sess), "subscription", before, f, family_id=family_id)
    db.commit()
    import logging
    logging.getLogger("kidctl.billing").info("Familie %s: Abo gesetzt von %s (%s, %s)", family_id,
                                              sess.operator.username, plan, status)
    await billing.apply(family_id)
    return local_redirect(f"/admin/families/{family_id}?saved=1#abo", status_code=303)


# ---------- Support-Ansicht (nur mit Freigabe der Familie) ----------

@router.post("/support/{family_id}")
def support_open(family_id: int, sess: OperatorSession = Depends(opr.operator_csrf),
                 db: Session = Depends(get_platform_db)):
    raw = support.open_view(db, sess, family_id)
    if raw is None:
        raise HTTPException(403, "Keine Freigabe dieser Familie")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(support.COOKIE, raw, max_age=support.VIEW_HOURS * 3600, httponly=True,
                    secure=settings.cookie_secure, samesite="strict", path="/")
    return resp


@router.get("/support/end")
def support_end(request: Request, db: Session = Depends(get_platform_db)):
    """Support-Ansicht beenden (Link im Banner); ohne Betreiber-Anmeldung nötig, löscht nur die eigene Ansicht."""
    support.close_view(db, request)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(support.COOKIE, path="/")
    return resp


# ---------- Einladungscodes ----------

def _codes_page(request, db, sess, status_code=200, **ctx):
    codes = db.scalars(select(SignupCode).order_by(SignupCode.created_at.desc())).all()
    return _render(request, "admin_codes.html", sess, status_code, codes=codes, now=utcnow(), days=opr.CODE_DAYS, **ctx)


@router.get("/codes")
def codes(request: Request, sess: OperatorSession = Depends(opr.require_admin),
          db: Session = Depends(get_platform_db)):
    return _codes_page(request, db, sess)


@router.post("/codes")
def code_create(request: Request, note: str = Form(""), free: str = Form(""),
                sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    code = opr.create_code(db, note, sess.operator_id, "free" if free else "")
    audit.record(db, audit.actor_of(sess), "code", "angelegt", None, (note.strip() or "–") + (" (Kostenlos)" if free else ""))
    db.commit()
    return _codes_page(request, db, sess, new_code=code)


@router.post("/codes/{code_id}/delete")
def code_delete(code_id: int, sess: OperatorSession = Depends(opr.admin_csrf),
                db: Session = Depends(get_platform_db)):
    code = db.get(SignupCode, code_id)
    if code is None:
        raise HTTPException(404)
    if code.used_at is not None:
        raise HTTPException(400, "Eingelöste Codes bleiben zur Nachvollziehbarkeit stehen")
    audit.record(db, audit.actor_of(sess), "code", "gelöscht", code.note or "–", None, object_id=code.id)
    db.delete(code)
    db.commit()
    return RedirectResponse("/admin/codes", status_code=303)


# ---------- Verwaltungsprotokoll ----------

LOG_PAGE = 50
LOG_OBJECTS = {"family": N_("Familie"), "subscription": N_("Abo"), "code": N_("Einladungscode"),
               "ticket": N_("Ticket"), "invoice": N_("Rechnung"), "invoice_settings": N_("Rechnungs-Einstellungen"),
               "price": N_("Preisliste"), "operator": N_("Betreiber-Zugang"),
               "branding": N_("Erscheinungsbild")}


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
    from sqlalchemy import func
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


# ---------- Eigenes Profil ----------

TOTP_CHANGE_MINUTES = 15
PROFILE_MESSAGES = {"1": N_("Gespeichert."), "password": N_("Passwort geändert. Andere Sitzungen wurden abgemeldet."),
                    "totp": N_("Neuer Authenticator eingerichtet. Andere Sitzungen wurden abgemeldet."),
                    "sessions": N_("Alle anderen Sitzungen wurden abgemeldet.")}


def _profile(request: Request, sess: OperatorSession, db: Session, status_code: int = 200, **ctx):
    others = db.scalar(select(func.count()).select_from(OperatorSession).where(
        OperatorSession.operator_id == sess.operator_id, OperatorSession.id != sess.id,
        OperatorSession.expires_at > utcnow()))
    msg = PROFILE_MESSAGES.get(request.query_params.get("saved", ""))
    return _render(request, "admin_profile.html", sess, status_code, roles=opr.ROLES, other_sessions=others,
                   message=_(msg) if msg else None, **ctx)


def _check_operator_password(db: Session, request: Request, op: Operator, password: str) -> str | None:
    """Aktuelles Passwort prüfen – mit demselben Fehlversuchszähler wie die Betreiber-Anmeldung."""
    ip, key = client_ip(request), f"op:{op.username}"[:64]
    if is_rate_limited(db, "admin", ip, key):
        return _("Zu viele Fehlversuche. Bitte später erneut versuchen.")
    if not verify_password(password, op.password_hash):
        record_failure(db, "admin", ip, key)
        db.commit()
        return _("Aktuelles Passwort ist falsch.")
    return None


def _end_other_sessions(db: Session, sess: OperatorSession) -> None:
    db.execute(delete(OperatorSession).where(OperatorSession.operator_id == sess.operator_id,
                                             OperatorSession.id != sess.id))


def _notify_operator(op: Operator, subject: str, text: str) -> None:
    """Sicherheitshinweis an die hinterlegte Adresse des Betreibers (falls vorhanden)."""
    from .. import mail
    if op.email:
        mail.send(op.email, f"KidCTL: {_(subject)}",
                  _("Hallo {name},").format(name=op.username) + f"\n\n{_(text)}\n\n"
                  + _("Falls du das nicht warst, setze den Zugang sofort per Kommandozeile zurück "
                      "(create-operator --reset) und prüfe das Verwaltungsprotokoll.") + "\n")


def _totp_token(op_id: int, secret: str, expires: int) -> str:
    """Signiert den vorbereiteten Schlüssel für das Bestätigungsformular (kein Serverzustand nötig)."""
    import hashlib
    import hmac
    sig = hmac.new(settings.secret_key.encode(), f"op-totp:{op_id}:{secret}:{expires}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{secret}.{expires}.{sig}"


def _totp_from_token(op_id: int, token: str) -> str | None:
    import hmac
    import time
    try:
        secret, expires, _sig = token.split(".")
        expires_at = int(expires)
    except ValueError:
        return None
    if expires_at < time.time() or not hmac.compare_digest(_totp_token(op_id, secret, expires_at), token):
        return None
    return secret


def _totp_page(request, sess, token: str, secret: str, status_code: int = 200, error: str | None = None):
    from .. import accounts
    from ..security import totp_qr_svg
    uri = accounts.totp_uri_for(secret, sess.operator.username)
    return _render(request, "admin_profile_totp.html", sess, status_code, token=token, secret=secret,
                   qr_svg=totp_qr_svg(uri), minutes=TOTP_CHANGE_MINUTES, error=error)


@router.get("/profile")
def profile(request: Request, sess: OperatorSession = Depends(opr.require_operator),
            db: Session = Depends(get_platform_db)):
    return _profile(request, sess, db)


@router.post("/profile")
def profile_save(request: Request, email: str = Form(""), ticket_mails: str = Form(""),
                 sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    from .. import accounts
    email = email.strip()[:255]
    if email and (err := accounts.email_error(email)):
        return _profile(request, sess, db, 400, error=err)
    if ticket_mails and not email:
        return _profile(request, sess, db, 400, error=_("Für Ticket-Mails bitte eine E-Mail-Adresse angeben."))
    op = db.get(Operator, sess.operator_id)
    old_email = op.email
    op.email, op.ticket_mails = email, bool(ticket_mails)
    if old_email != email:
        audit.record(db, audit.actor_of(sess), "operator", "email", old_email, email, object_id=op.id)
    db.commit()
    return RedirectResponse("/admin/profile?saved=1", status_code=303)


@router.post("/profile/password")
def profile_password(request: Request, current_password: str = Form(""), password: str = Form(""),
                     repeat: str = Form(""), sess: OperatorSession = Depends(opr.operator_csrf),
                     db: Session = Depends(get_platform_db)):
    from .. import accounts
    from ..security import hash_password
    op = db.get(Operator, sess.operator_id)
    error = _check_operator_password(db, request, op, current_password) or accounts.password_error(password, repeat)
    if error:
        return _profile(request, sess, db, 400, card_error=error, card="password")
    op.password_hash = hash_password(password)
    _end_other_sessions(db, sess)
    audit.record(db, audit.actor_of(sess), "operator", "password", object_id=op.id)
    db.commit()
    _notify_operator(op, N_("Passwort geändert"), N_("das Passwort deines Betreiber-Zugangs wurde geändert."))
    return RedirectResponse("/admin/profile?saved=password", status_code=303)


@router.post("/profile/totp")
def profile_totp_start(request: Request, sess: OperatorSession = Depends(opr.operator_csrf)):
    """Neuen Authenticator vorbereiten – der alte gilt, bis der neue bestätigt ist."""
    import time
    from ..security import new_totp_secret
    secret = new_totp_secret()
    return _totp_page(request, sess, _totp_token(sess.operator_id, secret, int(time.time()) + TOTP_CHANGE_MINUTES * 60),
                      secret)


@router.post("/profile/totp/confirm")
def profile_totp_confirm(request: Request, token: str = Form(""), current_password: str = Form(""),
                         totp: str = Form(""), sess: OperatorSession = Depends(opr.operator_csrf),
                         db: Session = Depends(get_platform_db)):
    from .. import accounts
    op = db.get(Operator, sess.operator_id)
    secret = _totp_from_token(op.id, token)
    if secret is None:
        return _profile(request, sess, db, 400, card_error=_("Die Einrichtung ist abgelaufen. Bitte neu starten."),
                        card="totp")
    error = _check_operator_password(db, request, op, current_password)
    step = accounts.totp_step(secret, totp)
    if not error and step is None:
        error = _("Der Code passt nicht zum neuen QR-Code.")
    if error:
        return _totp_page(request, sess, token, secret, 400, error)
    op.totp_secret, op.totp_last_step = secret, step
    _end_other_sessions(db, sess)
    audit.record(db, audit.actor_of(sess), "operator", "totp", object_id=op.id)
    db.commit()
    _notify_operator(op, N_("Authenticator geändert"),
                     N_("für deinen Betreiber-Zugang wurde ein neuer Authenticator eingerichtet."))
    return RedirectResponse("/admin/profile?saved=totp", status_code=303)


@router.post("/profile/sessions")
def profile_sessions(sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    """Alle anderen Sitzungen dieses Betreibers beenden (z. B. vergessener Rechner)."""
    _end_other_sessions(db, sess)
    audit.record(db, audit.actor_of(sess), "operator", "sessions", object_id=sess.operator_id)
    db.commit()
    return RedirectResponse("/admin/profile?saved=sessions", status_code=303)


# ---------- Tickets ----------

TICKET_SORTS = {"created": lambda t: t.created_at, "updated": lambda t: t.updated_at, "id": lambda t: t.id}


@router.get("/tickets")
def ticket_list(request: Request, status: str = "open", category: str = "", family: str = "", q: str = "",
                sort: str = "created", page: int = 1, sess: OperatorSession = Depends(opr.require_operator),
                db: Session = Depends(get_platform_db)):
    """Alle Tickets: Filter Status (Standard „offen“ = alles außer geschlossen), Kategorie, Familie, Freitext im
    Betreff (entschlüsselt im Server), älteste zuerst; 50 je Seite, Parameter in der URL."""
    from urllib.parse import urlencode
    from ..models import Family, Ticket
    query = select(Ticket)
    if status == "open":
        query = query.where(Ticket.status != "closed")
    elif status in tickets.STATUSES:
        query = query.where(Ticket.status == status)
    if category in tickets.CATEGORIES:
        query = query.where(Ticket.category == category)
    if family.strip().lstrip("#").isdigit():
        query = query.where(Ticket.family_id == int(family.strip().lstrip("#")))
    rows = db.scalars(query).all()
    if q.strip():
        needle = q.strip().casefold()
        rows = [t for t in rows if needle in t.subject.casefold()]
    key = sort.lstrip("-")
    rows.sort(key=TICKET_SORTS.get(key, TICKET_SORTS["created"]), reverse=sort.startswith("-"))
    pages = max(1, -(-len(rows) // opr.PAGE_SIZE))
    page = min(max(1, page), pages)
    shown = rows[(page - 1) * opr.PAGE_SIZE:page * opr.PAGE_SIZE]
    names = dict(db.execute(select(Family.id, Family.name).where(Family.id.in_({t.family_id for t in shown}))).all()) \
        if shown else {}
    params = {k: v for k, v in {"status": status, "category": category, "family": family, "q": q.strip(),
                                "sort": sort}.items() if v and not (k == "status" and v == "open")
              and not (k == "sort" and v == "created")}

    def url(**change):
        merged = {**params, "page": 1, **change}
        merged = {k: v for k, v in merged.items() if v not in ("", None) and not (k == "page" and v == 1)}
        return "/admin/tickets" + (f"?{urlencode(merged)}" if merged else "")
    return _render(request, "admin_tickets.html", sess, tickets=shown, total=len(rows), page=page, pages=pages,
                   url=url, names=names, categories=tickets.CATEGORIES, statuses=tickets.STATUSES,
                   f={"status": status, "category": category, "family": family, "q": q, "sort": sort})


def _admin_ticket(db: Session, ticket_id: int):
    from ..models import Ticket
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404)
    return t


def _ticket_page(request, db, sess, t, status_code=200, **ctx):
    import json
    from ..models import Family
    family = db.get(Family, t.family_id)
    try:
        meta = json.loads(t.meta) if t.meta else {}
    except ValueError:
        meta = {}
    return _render(request, "admin_ticket.html", sess, status_code, t=t, family=family, meta=meta,
                   categories=tickets.CATEGORIES, statuses=tickets.STATUSES, **ctx)


@router.get("/tickets/{ticket_id}")
def ticket_view(ticket_id: int, request: Request, sess: OperatorSession = Depends(opr.require_operator),
                db: Session = Depends(get_platform_db)):
    return _ticket_page(request, db, sess, _admin_ticket(db, ticket_id))


@router.post("/tickets/{ticket_id}")
def ticket_reply(ticket_id: int, request: Request, background: BackgroundTasks, body: str = Form(""),
                 internal: str = Form(""), status: str = Form("waiting"),
                 sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    t = _admin_ticket(db, ticket_id)
    try:
        notify = tickets.support_reply(db, t, audit.actor_of(sess), body, bool(internal), status)
    except tickets.TicketError as e:
        return _ticket_page(request, db, sess, t, 400, error=str(e), body=body)
    if notify:
        background.add_task(tickets.notify_family, t.family_id, t.id, False)
    return local_redirect(f"/admin/tickets/{t.id}#ende", status_code=303)


@router.post("/tickets/{ticket_id}/status")
def ticket_status(ticket_id: int, request: Request, status: str = Form(""),
                  sess: OperatorSession = Depends(opr.operator_csrf), db: Session = Depends(get_platform_db)):
    t = _admin_ticket(db, ticket_id)
    try:
        tickets.support_status(db, t, audit.actor_of(sess), status)
    except tickets.TicketError as e:
        return _ticket_page(request, db, sess, t, 400, error=str(e))
    return local_redirect(f"/admin/tickets/{t.id}", status_code=303)


# ---------- Rechnungen (nur Admin) ----------

from datetime import date as _date_type  # noqa: E402

from .. import invoices  # noqa: E402

SETTING_FIELDS = {"sender_name": 128, "sender_street": 128, "sender_postal_code": 16, "sender_city": 64,
                  "sender_country": 64, "tax_id": 64, "contact": 255, "bank_holder": 128, "iban": 64, "bic": 16,
                  "footer": 2000}


def _invoice(db: Session, invoice_id: int):
    from ..models import Invoice
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404)
    return inv


@router.get("/invoices")
def invoice_list(request: Request, year: str = "", status: str = "", family: str = "", page: int = 1,
                 sess: OperatorSession = Depends(opr.require_admin), db: Session = Depends(get_platform_db)):
    from urllib.parse import urlencode
    from sqlalchemy import func
    from ..models import Invoice
    q = select(Invoice)
    if year.isdigit():
        q = q.where(Invoice.year == int(year))
    if status in invoices.STATUSES:
        q = q.where(Invoice.status == status)
    if family.strip().lstrip("#").isdigit():
        q = q.where(Invoice.family_id == int(family.strip().lstrip("#")))
    rows = db.scalars(q.order_by(Invoice.status != "draft", Invoice.year.desc(), Invoice.seq.desc(),
                                 Invoice.id.desc())).all()
    pages = max(1, -(-len(rows) // opr.PAGE_SIZE))
    page = min(max(1, page), pages)
    sums = db.execute(select(Invoice.year, func.count(), func.sum(Invoice.net_cents), func.sum(Invoice.tax_cents),
                             func.sum(Invoice.gross_cents)).where(Invoice.year.is_not(None))
                      .group_by(Invoice.year).order_by(Invoice.year.desc())).all()
    params = {k: v for k, v in {"year": year, "status": status, "family": family}.items() if v}

    def url(**change):
        merged = {k: v for k, v in {**params, "page": 1, **change}.items() if v not in ("", None) and not (k == "page" and v == 1)}
        return "/admin/invoices" + (f"?{urlencode(merged)}" if merged else "")
    return _render(request, "admin_invoices.html", sess, invoices=rows[(page - 1) * opr.PAGE_SIZE:page * opr.PAGE_SIZE],
                   total=len(rows), page=page, pages=pages, url=url, sums=sums, statuses=invoices.STATUSES,
                   eur=invoices.eur, f={"year": year, "status": status, "family": family},
                   settings_missing=invoices.missing_settings(invoices.settings_row(db)))


@router.get("/invoices/export/{year}.csv")
def invoice_export_csv(year: int, sess: OperatorSession = Depends(opr.require_admin),
                       db: Session = Depends(get_platform_db)):
    from fastapi.responses import Response
    return Response(invoices.export_csv(db, year).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rechnungen-{year}.csv"'})


@router.get("/invoices/export/{year}.zip")
def invoice_export_zip(year: int, sess: OperatorSession = Depends(opr.require_admin),
                       db: Session = Depends(get_platform_db)):
    from fastapi.responses import Response
    return Response(invoices.export_zip(db, year), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="rechnungen-{year}.zip"'})


def _settings_page(request, db, sess, status_code=200, **ctx):
    from ..models import PriceItem
    row = invoices.settings_row(db)
    db.commit()
    return _render(request, "admin_invoice_settings.html", sess, status_code, s=row,
                   missing=invoices.missing_settings(row), prices=db.scalars(select(PriceItem).order_by(PriceItem.id)).all(),
                   eur=invoices.eur, tax_mode=settings.invoice_tax_mode, number_format=settings.invoice_number_format,
                   due_days=settings.invoice_due_days, **ctx)


@router.get("/invoice-settings")
def invoice_settings(request: Request, sess: OperatorSession = Depends(opr.require_admin),
                     db: Session = Depends(get_platform_db)):
    return _settings_page(request, db, sess, saved=request.query_params.get("saved"))


@router.post("/invoice-settings")
async def invoice_settings_save(request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                                db: Session = Depends(get_platform_db)):
    form = await request.form()
    row = invoices.settings_row(db)
    before = audit.snapshot(row, list(SETTING_FIELDS))
    for field, n in SETTING_FIELDS.items():
        value = str(form.get(field, "")).strip()[:n]
        if field == "iban":
            value = value.replace(" ", "").upper()
        setattr(row, field, value)
    audit.changes(db, audit.actor_of(sess), "invoice_settings", before, row)
    db.commit()
    return RedirectResponse("/admin/invoice-settings?saved=1", status_code=303)


@router.post("/invoice-settings/prices")
def price_save(request: Request, item_id: str = Form(""), label: str = Form(""), amount: str = Form(""),
               months: str = Form("12"), active: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
               db: Session = Depends(get_platform_db)):
    from ..models import PriceItem
    try:
        cents, n = invoices.parse_eur(amount), int(months)
        if not label.strip() or n < 1 or n > 120:
            raise ValueError
    except ValueError:
        return _settings_page(request, db, sess, 400, price_error=_("Bitte Bezeichnung, Betrag und Laufzeit (1–120 Monate) angeben."))
    item = db.get(PriceItem, int(item_id)) if item_id.isdigit() else None
    if item is None:
        item = PriceItem(label="", amount_cents=0)
        db.add(item)
        db.flush()
        before = {"label": None, "amount_cents": None, "months": None, "active": None}
    else:
        before = audit.snapshot(item, ["label", "amount_cents", "months", "active"])
    item.label, item.amount_cents, item.months, item.active = label.strip()[:200], cents, n, bool(active)
    audit.changes(db, audit.actor_of(sess), "price", before, item, object_id=item.id)
    db.commit()
    return RedirectResponse("/admin/invoice-settings?saved=1#preise", status_code=303)


@router.post("/families/{family_id}/invoices")
def invoice_create(family_id: int, price_item: str = Form(""), sess: OperatorSession = Depends(opr.admin_csrf),
                   db: Session = Depends(get_platform_db)):
    from ..models import Family, PriceItem
    f = db.get(Family, family_id)
    if f is None or f.wrapped_key is None:
        raise HTTPException(404)
    item = db.get(PriceItem, int(price_item)) if price_item.isdigit() else None
    inv = invoices.create_draft(db, f, item, audit.actor_of(sess))
    return local_redirect(f"/admin/invoices/{inv.id}", status_code=303)


def _invoice_page(request, db, sess, inv, status_code=200, **ctx):
    import json
    from ..models import Family, Invoice
    family = db.get(Family, inv.family_id)
    related = db.scalars(select(Invoice).where((Invoice.storno_of == inv.id) | (Invoice.id == inv.storno_of))).all()
    return _render(request, "admin_invoice.html", sess, status_code, inv=inv, family=family, related=related,
                   problems=invoices.issue_problems(db, inv) if inv.status == "draft" else [],
                   address=json.loads(inv.address) if inv.address else None, eur=invoices.eur,
                   preview=invoices.totals(inv.lines, inv.tax_mode or settings.invoice_tax_mode),
                   statuses=invoices.STATUSES, expires=invoices.expires_on(inv), today=invoices.today(), **ctx)


@router.get("/invoices/{invoice_id}")
def invoice_view(invoice_id: int, request: Request, sess: OperatorSession = Depends(opr.require_admin),
                 db: Session = Depends(get_platform_db)):
    return _invoice_page(request, db, sess, _invoice(db, invoice_id), saved=request.query_params.get("saved"))


@router.post("/invoices/{invoice_id}")
async def invoice_save(invoice_id: int, request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                       db: Session = Depends(get_platform_db)):
    """Entwurf speichern (Leistungszeitraum, Positionen). Ausgestellte Rechnungen: 409."""
    inv = _invoice(db, invoice_id)
    if inv.status != "draft":
        raise HTTPException(409, "Ausgestellte Rechnungen sind unveränderlich")
    form = await request.form()
    try:
        start = _date_type.fromisoformat(str(form.get("service_start", ""))) if form.get("service_start") else None
        end = _date_type.fromisoformat(str(form.get("service_end", ""))) if form.get("service_end") else None
        lines = []
        for text, qty, price in zip(form.getlist("line_text"), form.getlist("line_qty"), form.getlist("line_price")):
            if not str(text).strip():
                continue
            lines.append((str(text).strip(), int(qty or 1), invoices.parse_eur(str(price))))
        invoices.update_draft(db, inv, start, end, lines)
    except (ValueError, invoices.InvoiceError) as e:
        db.rollback()
        msg = str(e) if isinstance(e, invoices.InvoiceError) else _("Bitte Datum, Menge und Betrag prüfen.")
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=msg)
    return local_redirect(f"/admin/invoices/{inv.id}?saved=1", status_code=303)


@router.post("/invoices/{invoice_id}/delete")
def invoice_delete(invoice_id: int, sess: OperatorSession = Depends(opr.admin_csrf),
                   db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    family_id = inv.family_id
    try:
        invoices.delete_draft(db, inv, audit.actor_of(sess))
    except invoices.InvoiceError as e:
        raise HTTPException(409, str(e))
    return local_redirect(f"/admin/families/{family_id}#rechnungen", status_code=303)


def _send_later(invoice_id: int, actor: tuple, again: bool) -> None:
    from ..models import Invoice
    from ..tenant import platform_session
    with platform_session() as db:
        inv = db.get(Invoice, invoice_id)
        try:
            if inv is not None:
                invoices.send(db, inv, actor, again)
        except invoices.InvoiceError as e:
            import logging
            logging.getLogger("kidctl.invoices").warning("Rechnung %s nicht gesendet: %s", invoice_id, e)


@router.post("/invoices/{invoice_id}/issue")
def invoice_issue(invoice_id: int, request: Request, background: BackgroundTasks,
                  sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    try:
        invoices.issue(db, inv, audit.actor_of(sess))
    except invoices.InvoiceError as e:
        db.rollback()
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=str(e))
    background.add_task(_send_later, inv.id, audit.actor_of(sess), False)
    return local_redirect(f"/admin/invoices/{inv.id}?saved=issued", status_code=303)


@router.post("/invoices/{invoice_id}/paid")
async def invoice_paid(invoice_id: int, request: Request, paid_on: str = Form(""),
                       sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    try:
        day = _date_type.fromisoformat(paid_on) if paid_on else invoices.today()
        family_id = invoices.mark_paid(db, inv, day, audit.actor_of(sess))
    except (ValueError, invoices.InvoiceError) as e:
        db.rollback()
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=str(e))
    if family_id:
        await billing.apply(family_id)          # Ruhemodus endet sofort, Policies an die Agenten
    return local_redirect(f"/admin/invoices/{inv.id}?saved=paid", status_code=303)


@router.post("/invoices/{invoice_id}/storno")
def invoice_storno(invoice_id: int, request: Request, background: BackgroundTasks,
                   sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    was_paid = inv.status == "paid"
    try:
        s = invoices.storno(db, inv, audit.actor_of(sess))
    except invoices.InvoiceError as e:
        db.rollback()
        return _invoice_page(request, db, sess, _invoice(db, invoice_id), 400, error=str(e))
    background.add_task(_send_later, s.id, audit.actor_of(sess), False)
    return local_redirect(f"/admin/invoices/{s.id}?saved={'storno_paid' if was_paid else 'storno'}", status_code=303)


@router.post("/invoices/{invoice_id}/resend")
def invoice_resend(invoice_id: int, request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                   db: Session = Depends(get_platform_db)):
    inv = _invoice(db, invoice_id)
    try:
        ok = invoices.send(db, inv, audit.actor_of(sess), again=True)
    except invoices.InvoiceError as e:
        return _invoice_page(request, db, sess, inv, 400, error=str(e))
    if not ok:
        return _invoice_page(request, db, sess, inv, 502, error=_("Mail konnte nicht gesendet werden (SMTP prüfen)."))
    return local_redirect(f"/admin/invoices/{inv.id}?saved=sent", status_code=303)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, sess: OperatorSession = Depends(opr.require_admin),
                db: Session = Depends(get_platform_db)):
    from fastapi.responses import Response
    inv = _invoice(db, invoice_id)
    if inv.status == "draft":
        raise HTTPException(404)
    return Response(invoices.pdf_bytes(inv), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv.number}.pdf"', "Cache-Control": "no-store"})



# ---------- Erscheinungsbild (Whitelabel) ----------

BRAND_ERRORS = {"too_big": N_("Das Bild ist zu groß (höchstens 2 MB und 25 Megapixel)."),
                "format": N_("Bitte ein Bild als PNG, JPEG, WebP, GIF oder ICO hochladen.")}


def _branding_row(db: Session):
    from ..models import Branding
    row = db.get(Branding, 1)
    if row is None:
        row = Branding(id=1, name="")
        db.add(row)
        db.flush()
    return row


def _branding_page(request, db, sess, status_code: int = 200, **ctx):
    from .. import branding
    from ..models import Branding
    row = db.get(Branding, 1)
    return _render(request, "admin_branding.html", sess, status_code, row=row, b=branding.current(),
                   max_mb=branding.MAX_UPLOAD // (1024 * 1024), saved=request.query_params.get("saved"), **ctx)


@router.get("/branding")
def branding_page(request: Request, sess: OperatorSession = Depends(opr.require_admin),
                  db: Session = Depends(get_platform_db)):
    return _branding_page(request, db, sess)


@router.post("/branding")
async def branding_save(request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                        db: Session = Depends(get_platform_db)):
    """Name, Logo und App-Icon speichern; jede Änderung ins Verwaltungsprotokoll (Bilder nur als „neu“/„entfernt“)."""
    from .. import branding
    form = await request.form()
    row = _branding_row(db)
    actor = audit.actor_of(sess)
    name = " ".join(str(form.get("name", "")).split())[:64]
    if name != row.name:
        audit.record(db, actor, "branding", "name", row.name, name)
        row.name = name
    logo_only = bool(form.get("logo_only"))
    images = {}
    for kind, normalize in (("logo", branding.normalize_logo), ("icon", branding.normalize_icon)):
        upload = form.get(kind)
        data = await upload.read(branding.MAX_UPLOAD + 1) if upload is not None and hasattr(upload, "read") else b""
        if data:
            try:
                images[kind] = normalize(data)
            except branding.ImageError as e:
                db.rollback()
                return _branding_page(request, db, sess, 400, error=_(BRAND_ERRORS[str(e)]))
        elif form.get(f"remove_{kind}") and getattr(row, kind) is not None:
            images[kind] = None
    for kind, data in images.items():
        audit.record(db, actor, "branding", kind, None, _("neu") if data else _("entfernt"))
        setattr(row, kind, data)
    logo_only = logo_only and row.logo is not None
    if logo_only != row.logo_only:
        audit.record(db, actor, "branding", "logo_only", row.logo_only, logo_only)
        row.logo_only = logo_only
    row.updated_at = utcnow()
    db.commit()
    branding.invalidate()
    return RedirectResponse("/admin/branding?saved=1", status_code=303)
