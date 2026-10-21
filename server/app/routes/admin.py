"""Eltern-WebUI: Übersicht, Kinder, Geräte, Eltern-Accounts."""

from __future__ import annotations

import asyncio
from datetime import date, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kidctl_common.protocol import CLOSE_REVOKED, MAX_BODY, SUPPORTED_VERSIONS

from ..config import settings
from ..db import get_db
from ..models import (
    BlocklistState, Child, DayException, Device, Message, PairingCode, Parent, ParentSession, WeekdayRule,
    utcnow,
)
from .. import access, accounts, blocklog, chat, i18n, nowplaying, policy, programs, report, stats, webfilter
from .. import lock as lk
from .. import schedule as sch
from ..i18n import N_, _
from ..security import (
    client_ip, csrf_protect, hash_password, is_rate_limited, new_pairing_code, new_totp_secret, record_failure,
    require_session, token_hash, totp_qr_svg, totp_uri, verify_password,
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
    chat.expire_stale_requests(db)
    return render(request, "dashboard.html", children=children, devices=devices, online=manager.is_online,
                  pending=chat.pending_count(db), status={c.id: policy.day_status(db, c) for c in children},
                  fmt_minutes=sch.fmt_minutes, is_locked=lk.is_locked, playing=nowplaying.for_children(db))


# ---------- Kinder ----------

@router.get("/children")
def children_list(request: Request, db: Session = Depends(get_db)):
    children = db.scalars(select(Child).order_by(Child.name)).all()
    chat.expire_stale_requests(db)
    return render(request, "children.html", children=children, pending=chat.pending_count(db))


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
                      error=_("Ein Kind namens „{name}“ existiert bereits.").format(name=name))
    return _redirect("/children")


@router.post("/children/{child_id}/delete", dependencies=[Depends(csrf_protect)])
async def child_delete(child_id: int, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    devices = chat.device_ids_for_child(db, child.id)
    db.delete(child)
    db.commit()
    await policy.push_policies(db, devices)
    return _redirect("/children")


def _child_page(request: Request, db: Session, child: Child, status_code: int = 200, **ctx):
    today = sch.today_local()
    exceptions = db.scalars(select(DayException).where(
        DayException.child_id == child.id, DayException.day >= today).order_by(DayException.day)).all()
    ctx.setdefault("credit_day", today.isoformat())
    return render(request, "child.html", status_code=status_code, child=child, name_of=programs.name_lookup(db),
                  catalog=(catalog := programs.catalog_for_child(db, child.id)),
                  catalog_groups=programs.grouped_catalog(catalog), collapsed_groups=programs.COLLAPSED_GROUPS,
                  program_modes=programs.MODES,
                  web=webfilter.child_settings(db, child.id), web_modes=webfilter.MODES,
                  bundles=webfilter.BUNDLES, blocklists=webfilter.BLOCKLISTS,
                  default_lists=webfilter.DEFAULT_LISTS,
                  list_states={s.id: s for s in db.scalars(select(BlocklistState))},
                  blocked=programs.blocked_log(db, child.id, limit=10),
                  rules=sch.rules_by_day(child), groups=sch.GROUPS, weekday_names=i18n.weekday_names(),
                  differs=sch.group_differs(child), exceptions=exceptions, today=today,
                  fmt_minutes=sch.fmt_minutes, max_quota=sch.MAX_QUOTA, max_extra=sch.MAX_EXTRA,
                  is_locked=lk.is_locked, lock_durations=lk.DURATIONS, max_grace=lk.MAX_GRACE_MINUTES,
                  playing=nowplaying.for_children(db).get(child.id, []), **ctx)


@router.get("/children/{child_id}")
def child_detail(child_id: int, request: Request, db: Session = Depends(get_db)):
    return _child_page(request, db, _get_or_404(db, Child, child_id))


@router.post("/children/{child_id}/mode", dependencies=[Depends(csrf_protect)])
def child_mode(child_id: int, weekdays_same: str = Form(""), db: Session = Depends(get_db)):
    """Schaltet nur die Darstellung um; die Regeln ändern sich erst beim Speichern."""
    child = _get_or_404(db, Child, child_id)
    child.weekdays_same = weekdays_same == "1"
    db.commit()
    return _redirect(f"/children/{child_id}")


@router.post("/children/{child_id}/schedule", dependencies=[Depends(csrf_protect)])
async def child_schedule(child_id: int, request: Request, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    try:
        sch.apply_schedule(child, await request.form())
    except sch.ScheduleError as e:
        db.rollback()
        return _child_page(request, db, child, status_code=400, error=str(e))
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}?saved=1")


@router.post("/children/{child_id}/credit", dependencies=[Depends(csrf_protect)])
async def child_credit(child_id: int, request: Request, day: str = Form(...), extra_minutes: str = Form("0"),
                 window_end: str = Form(""), extra_games: str = Form("0"), db: Session = Depends(get_db)):
    """Direkte Gutschrift ohne Anfrage = Tagesausnahme für genau ein Datum."""
    child = _get_or_404(db, Child, child_id)
    try:
        the_day, extra, override, games = sch.parse_credit(child, day, extra_minutes, window_end, extra_games)
    except sch.ScheduleError as e:
        return _child_page(request, db, child, status_code=400, error=str(e), credit_day=day)
    sch.add_credit(db, child, the_day, extra, override, games)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}")


def _back(child_id: int, back: str) -> str:
    return "/" if back == "dashboard" else f"/children/{child_id}"


@router.post("/children/{child_id}/lock", dependencies=[Depends(csrf_protect)])
async def child_lock(child_id: int, request: Request, duration: str = Form(""), grace: str = Form("1"),
                     back: str = Form(""), db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    try:
        lk.lock(child, duration, lk.parse_grace(grace))
    except ValueError as e:
        db.rollback()
        return _child_page(request, db, child, status_code=400, error=str(e))
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(_back(child_id, back))


@router.post("/children/{child_id}/unlock", dependencies=[Depends(csrf_protect)])
async def child_unlock(child_id: int, back: str = Form(""), db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    lk.unlock(child)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(_back(child_id, back))


@router.post("/children/{child_id}/programs", dependencies=[Depends(csrf_protect)])
async def child_programs(child_id: int, request: Request, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    form = await request.form()
    mode = str(form.get("program_mode", "off"))
    if mode not in programs.MODES:
        raise HTTPException(400, "Ungültiger Modus")
    child.program_mode = mode
    keys = {str(k) for k in form.getlist("app") if str(k).startswith(("exe:", "flatpak:", "snap:"))}
    programs.set_allowed(db, child.id, keys)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}?programs=1#programme")


@router.post("/children/{child_id}/programs/custom", dependencies=[Depends(csrf_protect)])
async def child_custom_add(child_id: int, request: Request, path: str = Form(""), name: str = Form(""),
                           db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    try:
        programs.add_custom(db, child.id, path, name)
    except ValueError as e:
        return _child_page(request, db, child, status_code=400, custom_error=str(e), custom_path=path[:500],
                           custom_name=name[:programs.MAX_CUSTOM_NAME])
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}?programs=1#programme")


@router.post("/children/{child_id}/programs/custom/{custom_id}/delete", dependencies=[Depends(csrf_protect)])
async def child_custom_delete(child_id: int, custom_id: int, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    if not programs.delete_custom(db, child.id, custom_id):
        raise HTTPException(404)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}?programs=1#programme")


@router.get("/children/{child_id}/protokoll")
def child_blocked(child_id: int, request: Request, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    f = blocklog.parse(request.query_params)
    today = sch.today_local()
    if f.kind == blocklog.GAME:
        rows, total = blocklog.sessions(db, child.id, f, today)
        days, top = blocklog.by_day(rows, today, "started_at"), blocklog.top_games(db, child.id, f, today)
    else:
        rows, total = blocklog.events(db, child.id, f, today)
        days, top = blocklog.by_day(rows, today), blocklog.top(db, child.id, f, today)
    return render(request, "blocked.html", child=child, f=f, base=f"/children/{child.id}/protokoll",
                  name_of=programs.name_lookup(db),
                  days=days, shown=len(rows), total=total, top=top, users=blocklog.users(db, child.id),
                  games=f.kind == blocklog.GAME, session_minutes=blocklog.session_minutes,
                  is_running=blocklog.is_running, launcher=blocklog.launcher, fmt_minutes=sch.fmt_minutes,
                  kinds=blocklog.KINDS, ranges=blocklog.RANGES, statuses=blocklog.STATUSES,
                  orders=blocklog.ORDERS, page=blocklog.PAGE, retention=blocklog.RETENTION_DAYS)


@router.get("/children/{child_id}/stats")
def child_stats(child_id: int, request: Request, range: int = 7, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    app_days = 1 if range == 1 else 7
    bars = stats.daily(db, child)
    scale = max([b.used_minutes for b in bars] + [b.allowed_minutes for b in bars] + [1])
    app_rows = stats.apps(db, child, app_days)
    return render(request, "stats.html", child=child, playing=nowplaying.for_children(db).get(child.id, []), bars=bars, scale=scale, sums=stats.week_sums(bars),
                  apps=app_rows, app_days=app_days, app_scale=max([r.minutes for r in app_rows] + [1]),
                  fmt_minutes=sch.fmt_minutes, weekday_names=i18n.weekday_names())


@router.post("/children/{child_id}/web", dependencies=[Depends(csrf_protect)])
async def child_web(child_id: int, request: Request, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    form = await request.form()
    mode = str(form.get("web_mode", "off"))
    if mode not in webfilter.MODES:
        raise HTTPException(400, "Ungültiger Modus")
    allow, bad_allow = webfilter.parse_domain_input(str(form.get("allow", "")))
    deny, bad_deny = webfilter.parse_domain_input(str(form.get("deny", "")))
    if bad_allow or bad_deny:
        return _child_page(request, db, child, status_code=400,
                           error=_("Ungültige Domain(s):") + " " + ", ".join((bad_allow + bad_deny)[:10]))
    lists = {str(x) for x in form.getlist("list")}
    webfilter.save_child_settings(db, child, mode, form.get("safesearch") == "1",
                                  {str(x) for x in form.getlist("bundle")}, allow, deny, lists)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    # Noch nie geladene Listen sofort holen (Agent erhält danach eine aktualisierte Policy)
    missing = {x for x in lists & webfilter.BLOCKLISTS.keys()
               if not getattr(db.get(BlocklistState, x), "sha256", "")}
    if mode == "blacklist" and missing:
        task = asyncio.create_task(_fetch_and_push(missing))
        _background.add(task)
        task.add_done_callback(_background.discard)
    return _redirect(f"/children/{child_id}?web=1#internet")


_background: set[asyncio.Task] = set()


async def _fetch_and_push(list_ids: set[str]) -> None:
    changed = await webfilter.refresh_lists(list_ids)
    if changed:
        await policy.push_for_lists(changed)


@router.post("/children/{child_id}/exceptions/{exc_id}/delete", dependencies=[Depends(csrf_protect)])
async def child_exception_delete(child_id: int, exc_id: int, db: Session = Depends(get_db)):
    exc = _get_or_404(db, DayException, exc_id)
    if exc.child_id != child_id:
        raise HTTPException(404)
    db.delete(exc)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child_id))
    return _redirect(f"/children/{child_id}")


# ---------- Chat & Verlängerungsanfragen ----------

CHAT_LIMIT = 100


def _chat_messages(db: Session, child: Child) -> list[Message]:
    chat.expire_stale_requests(db)
    rows = db.scalars(select(Message).where(Message.child_id == child.id)
                      .order_by(Message.id.desc()).limit(CHAT_LIMIT)).all()
    return list(reversed(rows))


def _chat_ctx(db: Session, child: Child) -> dict:
    return dict(child=child, messages=_chat_messages(db, child),
                sender_name=lambda m: chat.sender_name(db, m, child),
                weekday_names=i18n.weekday_names(), fmt_minutes=sch.fmt_minutes,
                max_extra=sch.MAX_EXTRA, has_device=bool(chat.device_ids_for_child(db, child.id)))


@router.get("/children/{child_id}/chat")
def child_chat(child_id: int, request: Request, db: Session = Depends(get_db)):
    return render(request, "chat.html", **_chat_ctx(db, _get_or_404(db, Child, child_id)))


@router.get("/children/{child_id}/chat/messages")
def child_chat_fragment(child_id: int, request: Request, db: Session = Depends(get_db)):
    """HTML-Fragment für die Live-Aktualisierung (Polling aus static/chat.js)."""
    return render(request, "_messages.html", **_chat_ctx(db, _get_or_404(db, Child, child_id)))


@router.post("/children/{child_id}/chat")
async def child_chat_send(child_id: int, request: Request, body: str = Form(...),
                          sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    body = body.strip()
    if not body or len(body) > MAX_BODY:
        return render(request, "chat.html", status_code=400, **_chat_ctx(db, child),
                      error=_("Nachricht muss 1 bis {max} Zeichen lang sein.").format(max=MAX_BODY))
    msg = Message(child_id=child.id, from_child=False, sender_parent_id=sess.parent_id, body=body)
    db.add(msg)
    db.commit()
    await chat.push_to_child(db, msg)
    return _redirect(f"/children/{child_id}/chat#ende")


@router.post("/children/{child_id}/requests/{msg_id}")
async def child_request_decide(child_id: int, msg_id: int, request: Request,
                               action: str = Form(...), minutes: str = Form("0"),
                               window_end: str = Form(""),
                               sess: ParentSession = Depends(csrf_protect),
                               db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    msg = _get_or_404(db, Message, msg_id)
    if msg.child_id != child.id or action not in {"approve", "reject"}:
        raise HTTPException(404)
    try:
        if action == "approve":
            extra = sch.parse_minutes(minutes or "0", "Zusätzliche Minuten", sch.MAX_EXTRA)
            override = sch.parse_time(window_end, "Fensterende") if window_end.strip() else None
            chat.decide(db, msg, sess.parent_id, True, extra, override)
        else:
            chat.decide(db, msg, sess.parent_id, False)
    except sch.ScheduleError as e:
        db.rollback()
        return render(request, "chat.html", status_code=400, **_chat_ctx(db, child), error=str(e))
    await chat.push_to_child(db, msg)
    if msg.status == "approved":
        await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/chat#m{msg.id}")


@router.post("/children/{child_id}/access/{msg_id}")
async def child_access_decide(child_id: int, msg_id: int, request: Request, action: str = Form(...),
                              sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Freigabe-Anfrage: dauerhaft erlauben, nur heute erlauben oder ablehnen."""
    child = _get_or_404(db, Child, child_id)
    msg = _get_or_404(db, Message, msg_id)
    if msg.child_id != child.id or action not in {"permanent", "today", "reject"}:
        raise HTTPException(404)
    try:
        access.decide(db, msg, sess.parent_id, None if action == "reject" else action)
    except access.AccessError as e:
        db.rollback()
        return render(request, "chat.html", status_code=400, **_chat_ctx(db, child), error=str(e))
    await chat.push_to_child(db, msg)
    if msg.status == "approved":
        await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/chat#m{msg.id}")


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
                  online=manager.is_online(device.id), supported=sorted(SUPPORTED_VERSIONS),
                  policy_current=policy.policy_current(db, device))


@router.post("/devices/{device_id}/mapping", dependencies=[Depends(csrf_protect)])
async def device_mapping(device_id: int, request: Request, db: Session = Depends(get_db)):
    device = _get_or_404(db, Device, device_id)
    form = await request.form()
    child_ids = set(db.scalars(select(Child.id)))
    for du in device.users:
        raw = str(form.get(f"user_{du.id}", ""))
        du.child_id = int(raw) if raw.isdigit() and int(raw) in child_ids else None
    db.commit()
    await policy.send_policy(db, device_id)
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
                  email: str = Form(""), password: str = Form(...), db: Session = Depends(get_db)):
    username, email = username.strip(), email.strip()
    error = (accounts.username_error(db, username) or accounts.email_error(email)
             or accounts.password_error(password))
    if error:
        parents = db.scalars(select(Parent).order_by(Parent.username)).all()
        return render(request, "parents.html", status_code=400, parents=parents, error=error)

    parent = Parent(username=username, display_name=display_name.strip()[:128], email=email[:255],
                    password_hash=hash_password(password), totp_secret=new_totp_secret())
    db.add(parent)
    db.commit()
    uri = totp_uri(parent)
    return render(request, "parent_created.html", parent=parent, uri=uri, qr_svg=totp_qr_svg(uri),
                  secret=parent.totp_secret)


@router.post("/parents/{parent_id}/totp-reset")
def parent_totp_reset(parent_id: int, request: Request, sess: ParentSession = Depends(csrf_protect),
                      db: Session = Depends(get_db)):
    """Authenticator eines anderen Elternteils zurücksetzen: altes Secret sofort ungültig, Sitzungen
    beendet; Einrichtungslink (24 h, verlangt zusätzlich das Passwort) zum Weitergeben."""
    if parent_id == sess.parent_id:
        raise HTTPException(400, "Eigenen Authenticator im Profil ändern")
    parent = _get_or_404(db, Parent, parent_id)
    secret = new_totp_secret()
    raw = accounts.create_token(db, parent, "totp_setup", timedelta(hours=accounts.TOTP_SETUP_HOURS), secret)
    parent.totp_secret, parent.totp_last_step = new_totp_secret(), 0   # alter Authenticator ungültig
    accounts.end_sessions(db, parent.id)
    db.commit()
    by = sess.parent.display_name or sess.parent.username
    accounts.notify(parent, N_("Authenticator zurückgesetzt"),
                    N_("{by} hat den Authenticator deines Accounts „{username}“ zurückgesetzt. "
                       "Den Einrichtungslink bekommst du von dieser Person."), by=by, username=parent.username)
    return render(request, "totp_reset_link.html", parent=parent, link=f"{settings.base_url}/totp-setup/{raw}",
                  hours=accounts.TOTP_SETUP_HOURS)


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


# ---------- Profil ----------

def _profile(request: Request, parent: Parent, status_code: int = 200, **ctx):
    return render(request, "profile.html", status_code=status_code, parent=parent,
                  mail_enabled=settings.mail_enabled, intervals=report.INTERVALS, send_hour=report.SEND_HOUR, **ctx)


_check_password = accounts.check_password


PROFILE_NOTICES = {"saved": N_("Profil gespeichert."), "password": N_("Passwort geändert. Andere Sitzungen wurden abgemeldet."),
                   "totp": N_("Neuer Authenticator eingerichtet. Andere Sitzungen wurden abgemeldet."),
                   "report": N_("Nutzungsbericht gespeichert."), "report_sent": N_("Nutzungsbericht gesendet.")}


@router.get("/profile")
def profile(request: Request):
    notice = PROFILE_NOTICES.get(request.query_params.get("msg", ""))
    return _profile(request, request.state.session.parent, notice=_(notice) if notice else None)


@router.post("/profile")
def profile_save(request: Request, username: str = Form(""), display_name: str = Form(""), email: str = Form(""),
                 language: str = Form(""), current_password: str = Form(""),
                 sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    username, email = username.strip(), email.strip()
    language = language if language in i18n.LANGS else ""
    error = accounts.username_error(db, username, parent.id) or accounts.email_error(email)
    sensitive = username != parent.username or email != parent.email
    if not error and sensitive:
        error = _check_password(db, request, parent, current_password)
    if error:
        return _profile(request, parent, 400, error=error, form={"username": username, "display_name": display_name,
                                                                   "email": email, "language": language})
    old_email = parent.email
    parent.username, parent.display_name, parent.email = username, display_name.strip()[:128], email[:255]
    parent.language = language
    db.commit()
    if old_email and old_email != email:
        with i18n.use(i18n.parent_language(parent)):
            accounts.mail.send(old_email, _("KidCTL: E-Mail-Adresse geändert"),
                               _("Die E-Mail-Adresse des KidCTL-Accounts „{username}“ wurde von dieser Adresse auf "
                                 "„{email}“ geändert.").format(username=username, email=email or _("(keine)"))
                               + "\n" + _("Falls du das nicht warst, melde dich sofort an.") + "\n")
    return _redirect("/profile?msg=saved")


@router.post("/profile/report")
def profile_report(request: Request, interval: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                   db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    if interval not in report.INTERVALS:
        return _profile(request, parent, 400, error=_("Ungültiger Zeitraum."))
    if interval and not parent.email:
        return _profile(request, parent, 400, error=_("Für den Nutzungsbericht zuerst eine E-Mail-Adresse eintragen."))
    if interval != parent.report_interval:
        report.activate(parent, interval, sch.today_local())
        db.commit()
    return _redirect("/profile?msg=report")


def _report_period(parent: Parent, interval: str) -> report.Period:
    interval = interval if interval in ("weekly", "monthly") else (parent.report_interval or "weekly")
    return report.last_period(interval, sch.today_local())


@router.post("/profile/report/send")
def profile_report_send(request: Request, interval: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                        db: Session = Depends(get_db)):
    """Bericht für den letzten abgeschlossenen Zeitraum sofort senden (zum Ausprobieren)."""
    parent = db.get(Parent, sess.parent_id)
    if not parent.email or not settings.mail_enabled:
        return _profile(request, parent, 400, error=_("Versand nicht möglich: keine E-Mail-Adresse bzw. kein SMTP eingerichtet."))
    if not report.send(db, parent, _report_period(parent, interval)):
        return _profile(request, parent, 502, error=_("Versand fehlgeschlagen (Details im Server-Log)."))
    return _redirect("/profile?msg=report_sent")


@router.get("/profile/report/preview", response_class=HTMLResponse)
def profile_report_preview(request: Request, interval: str = "", db: Session = Depends(get_db)):
    parent = db.get(Parent, request.state.session.parent_id)
    return HTMLResponse(report.render(db, parent, _report_period(parent, interval))[2])


@router.post("/profile/password")
def profile_password(request: Request, current_password: str = Form(""), password: str = Form(""),
                     repeat: str = Form(""), sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    error = _check_password(db, request, parent, current_password) or accounts.password_error(password, repeat)
    if error:
        return _profile(request, parent, 400, error=error)
    parent.password_hash = hash_password(password)
    accounts.end_sessions(db, parent.id, keep_session_id=sess.id)
    db.commit()
    accounts.notify(parent, N_("Passwort geändert"), N_("dein Passwort wurde im Profil geändert."))
    return _redirect("/profile?msg=password")


@router.post("/profile/totp")
def profile_totp_start(request: Request, sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Neuen Authenticator vorbereiten (der alte gilt, bis der neue bestätigt ist)."""
    parent = db.get(Parent, sess.parent_id)
    secret = new_totp_secret()
    raw = accounts.create_token(db, parent, "totp_change", timedelta(minutes=accounts.TOTP_CHANGE_MINUTES), secret)
    db.commit()
    return _totp_change_page(request, parent, raw, secret)


def _totp_change_page(request, parent, raw, secret, status_code=200, error=None):
    uri = accounts.totp_uri_for(secret, parent.username)
    return render(request, "profile_totp.html", status_code=status_code, parent=parent, token=raw, secret=secret,
                  qr_svg=totp_qr_svg(uri), minutes=accounts.TOTP_CHANGE_MINUTES, error=error)


@router.post("/profile/totp/confirm")
def profile_totp_confirm(request: Request, token: str = Form(""), current_password: str = Form(""),
                         totp: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                         db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    t = accounts.get_token(db, token, "totp_change")
    if t is None or t.parent_id != parent.id:
        return _profile(request, parent, 400, error=_("Die Einrichtung ist abgelaufen. Bitte neu starten."))
    error = _check_password(db, request, parent, current_password)
    step = accounts.totp_step(t.secret, totp)
    if not error and step is None:
        error = _("Der Code passt nicht zum neuen QR-Code.")
    if error:
        return _totp_change_page(request, parent, token, t.secret, 400, error)
    parent.totp_secret, parent.totp_last_step = t.secret, step
    t.used_at = utcnow()
    accounts.end_sessions(db, parent.id, keep_session_id=sess.id)
    db.commit()
    accounts.notify(parent, N_("Authenticator geändert"), N_("für deinen Account wurde ein neuer Authenticator eingerichtet."))
    return _redirect("/profile?msg=totp")
