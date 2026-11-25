"""Eltern-WebUI: Übersicht, Kinder, Geräte, Eltern-Accounts."""

from __future__ import annotations

import asyncio
import secrets
from datetime import time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kidctl_common.protocol import CLOSE_REVOKED, MAX_BODY, SUPPORTED_VERSIONS, UpdateNow

from ..config import settings
from ..db import get_db
from ..models import (
    ROLE_OWNER, BlocklistState, Child, DayException, Device, FamilyInvite, Membership, Message, PairingCode, Parent,
    ParentSession,
    WeekdayRule, utcnow,
)
from .. import (
    access, accounts, blocklog, chat, i18n, nowplaying, passkeys, policy, programs, report, selfupdate, stats, vacations,
    versions, webfilter,
)
from .. import lock as lk
from .. import schedule as sch
from .. import timebank
from ..i18n import N_, _
from ..middleware import csp
from ..security import (
    csrf_protect, hash_password, new_pairing_code, new_totp_secret, require_session, token_hash, totp_qr_svg,
)
from ..web import local_redirect, render
from . import profile_shared as ps
from ..ws import manager

router = APIRouter(dependencies=[Depends(require_session)])


def _redirect(url: str) -> RedirectResponse:
    return local_redirect(url)


def _get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404)
    return obj


# ---------- Übersicht ----------

def _by_name(rows) -> list:
    """Namen sind verschlüsselt (Reihenfolge in der Datenbank zufällig): im Server sortieren."""
    return sorted(rows, key=lambda r: (r.name or "").casefold())


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    children = _by_name(db.scalars(select(Child)))
    devices = _by_name(db.scalars(select(Device).where(Device.revoked_at.is_(None))))
    chat.expire_stale_requests(db)
    return render(request, "dashboard.html", children=children, devices=devices, online=manager.is_online,
                  pending=chat.pending_count(db), status={c.id: policy.day_status(db, c) for c in children},
                  fmt_minutes=sch.fmt_minutes, is_locked=lk.is_locked, playing=nowplaying.for_children(db))


# ---------- Kinder ----------

@router.get("/children")
def children_list(request: Request, db: Session = Depends(get_db)):
    children = _by_name(db.scalars(select(Child)))
    chat.expire_stale_requests(db)
    return render(request, "children.html", children=children, pending=chat.pending_count(db))


@router.post("/children", dependencies=[Depends(csrf_protect)])
def child_create(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return _redirect("/children")
    from .. import billing
    if limit_error := billing.child_limit_error(db):
        return render(request, "children.html", status_code=400, children=_by_name(db.scalars(select(Child))),
                      error=limit_error)
    child = Child(name=name, rules=[
        WeekdayRule(weekday=d, quota_minutes=60, window_start=time(8, 0), window_end=time(20, 0))
        for d in range(7)
    ])
    db.add(child)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        children = _by_name(db.scalars(select(Child)))
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


def _child_head(db: Session, child: Child) -> dict:
    """Was der gemeinsame Kopf aller Seiten eines Kindes braucht."""
    return dict(playing=nowplaying.for_children(db).get(child.id, []),
                pending_chat=chat.pending_count(db).get(child.id, 0), is_locked=lk.is_locked)


def _child_page(request: Request, db: Session, child: Child, tab: str = "heute", status_code: int = 200, **ctx):
    today = sch.today_local()
    devices = db.scalars(select(Device).where(Device.id.in_(chat.device_ids_for_child(db, child.id)))).all()
    cal = vacations.Calendar.load(db)
    if tab == "heute":
        ctx.setdefault("credit_day", today.isoformat())
        ctx.update(status=policy.day_status(db, child, cal=cal), day_label=vacations.day_label(cal, today), name_of=programs.name_lookup(db),
                   blocked=programs.blocked_log(db, child.id, limit=5), fmt_minutes=sch.fmt_minutes,
                   online_devices=[d.name for d in devices if manager.is_online(d.id)],
                   web_mode_label=webfilter.MODES[child.web_mode], program_mode_label=programs.MODES[child.program_mode],
                   n_allowed=len(programs.allowed_keys(db, child.id)), max_extra=sch.MAX_EXTRA,
                   lock_durations=lk.DURATIONS, max_grace=lk.MAX_GRACE_MINUTES)
    elif tab == "zeiten":
        ctx.update(rules=sch.rules_by_day(child), groups=sch.GROUPS, weekday_names=i18n.weekday_names(),
                   differs=sch.group_differs(child), max_quota=sch.MAX_QUOTA, fmt_minutes=sch.fmt_minutes,
                   vacation_rules=sch.rules_by_day(child, vacation=True),
                   vacation_differs=sch.group_differs(child, vacation=True),
                   vacation_saved=bool(child.vacation_rules), special_days=cal.special_days(today),
                   holiday_region=cal.region,
                   max_blocks=sch.MAX_BLOCKS, old_devices=policy.old_protocol_devices(db, child),
                   bank=timebank.for_day(db, child, today, cal), tb=timebank,
                   exceptions=db.scalars(select(DayException).where(
                       DayException.child_id == child.id, DayException.day >= today).order_by(DayException.day)).all())
    elif tab == "internet":
        ctx.update(web=webfilter.child_settings(db, child.id), web_modes=webfilter.MODES, bundles=webfilter.BUNDLES,
                   blocklists=webfilter.BLOCKLISTS, default_lists=webfilter.DEFAULT_LISTS,
                   list_states={s.id: s for s in db.scalars(select(BlocklistState))})
    else:
        platforms = {d.platform for d in devices}
        catalog = programs.catalog_for_child(db, child.id)
        ctx.update(catalog=catalog, catalog_groups=programs.grouped_catalog(catalog), open_groups=programs.OPEN_GROUPS,
                   program_modes=programs.MODES, has_linux=not platforms or "linux" in platforms,
                   has_windows=not platforms or "windows" in platforms,
                   free_old_devices=programs.free_old_devices(devices) if any(e.free for e in catalog) else [])
    return render(request, f"child_{tab}.html", status_code=status_code, child=child, tab=tab, today=today,
                  **_child_head(db, child), **ctx)

@router.get("/children/{child_id}")
def child_detail(child_id: int, request: Request, db: Session = Depends(get_db)):
    return _child_page(request, db, _get_or_404(db, Child, child_id))


@router.get("/children/{child_id}/zeiten")
def child_times(child_id: int, request: Request, db: Session = Depends(get_db)):
    return _child_page(request, db, _get_or_404(db, Child, child_id), "zeiten")


@router.get("/children/{child_id}/internet")
def child_internet(child_id: int, request: Request, db: Session = Depends(get_db)):
    return _child_page(request, db, _get_or_404(db, Child, child_id), "internet")


@router.get("/children/{child_id}/programme")
def child_programs_page(child_id: int, request: Request, db: Session = Depends(get_db)):
    return _child_page(request, db, _get_or_404(db, Child, child_id), "programme")


@router.post("/children/{child_id}/mode", dependencies=[Depends(csrf_protect)])
def child_mode(child_id: int, weekdays_same: str = Form(""), db: Session = Depends(get_db)):
    """Schaltet nur die Darstellung um; die Regeln ändern sich erst beim Speichern."""
    child = _get_or_404(db, Child, child_id)
    child.weekdays_same = weekdays_same == "1"
    db.commit()
    return _redirect(f"/children/{child_id}/zeiten")


@router.post("/children/{child_id}/schedule", dependencies=[Depends(csrf_protect)])
async def child_schedule(child_id: int, request: Request, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    try:
        sch.apply_schedule(child, await request.form())
    except sch.ScheduleError as e:
        db.rollback()
        return _child_page(request, db, child, "zeiten", status_code=400, error=str(e))
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/zeiten?saved=1")


@router.post("/children/{child_id}/time-bank", dependencies=[Depends(csrf_protect)])
async def child_time_bank(child_id: int, request: Request, db: Session = Depends(get_db)):
    """Zeitkonto des Kindes (Schalter, Anteil, Deckel je Tag, Verfall)."""
    child = _get_or_404(db, Child, child_id)
    try:
        timebank.apply_settings(child, await request.form(), sch.today_local())
    except sch.ScheduleError as e:
        db.rollback()
        return _child_page(request, db, child, "zeiten", status_code=400, bank_error=str(e))
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/zeiten?saved=bank#zeitkonto")


@router.post("/children/{child_id}/vacation-schedule", dependencies=[Depends(csrf_protect)])
async def child_vacation_schedule(child_id: int, request: Request, db: Session = Depends(get_db)):
    """Ferienvorlage des Kindes (gilt an Tagen in einem Ferienzeitraum der Familie)."""
    child = _get_or_404(db, Child, child_id)
    try:
        sch.apply_schedule(child, await request.form(), vacation=True)
    except sch.ScheduleError as e:
        db.rollback()
        return _child_page(request, db, child, "zeiten", status_code=400, vacation_error=str(e))
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/zeiten?saved=vacation#ferien")


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
    return _redirect(f"/children/{child_id}?credited=1")


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
    prefixes = ("exe:", "flatpak:", "snap:", "appx:")
    keys = {str(k) for k in form.getlist("app") if str(k).startswith(prefixes)}
    programs.set_allowed(db, child.id, keys)
    games = {str(k)[:512] for k in form.getlist("game") if str(k).startswith(prefixes)}
    programs.set_games(db, child.id, games)
    free = {str(k)[:512] for k in form.getlist("free") if str(k).startswith(prefixes)}
    programs.set_free(db, child.id, programs.free_allowed(db, child.id, free, games))
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/programme?programs=1")


@router.post("/children/{child_id}/programs/custom", dependencies=[Depends(csrf_protect)])
async def child_custom_add(child_id: int, request: Request, path: str = Form(""), name: str = Form(""),
                           db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    try:
        programs.add_custom(db, child.id, path, name)
    except ValueError as e:
        return _child_page(request, db, child, "programme", status_code=400, custom_error=str(e), custom_path=path[:500],
                           custom_name=name[:programs.MAX_CUSTOM_NAME])
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/programme?programs=1")


@router.post("/children/{child_id}/programs/custom/{custom_id}/delete", dependencies=[Depends(csrf_protect)])
async def child_custom_delete(child_id: int, custom_id: int, db: Session = Depends(get_db)):
    child = _get_or_404(db, Child, child_id)
    if not programs.delete_custom(db, child.id, custom_id):
        raise HTTPException(404)
    db.commit()
    await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/programme?programs=1")


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
    return render(request, "blocked.html", child=child, tab="protokoll", **_child_head(db, child), f=f, base=f"/children/{child.id}/protokoll",
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
    return render(request, "stats.html", child=child, tab="stats", **_child_head(db, child), bars=bars, scale=scale, sums=stats.week_sums(bars),
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
        return _child_page(request, db, child, "internet", status_code=400,
                           error=_("Ungültige Domain(s):") + " " + ", ".join((bad_allow + bad_deny)[:10]))
    current = webfilter.child_settings(db, child.id)
    if webfilter.too_many_domains(allow, current["allow"]) or webfilter.too_many_domains(deny, current["deny"]):
        return _child_page(request, db, child, "internet", status_code=400,
                           error=_("Zu viele Domains: höchstens {max} je Feld (freigegeben: {allow}, gesperrt: {deny}).")
                           .format(max=webfilter.MAX_CUSTOM_DOMAINS, allow=len(allow), deny=len(deny)))
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
    return _redirect(f"/children/{child_id}/internet?web=1")


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
    return _redirect(f"/children/{child_id}/zeiten")


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
    child = _get_or_404(db, Child, child_id)
    return render(request, "chat.html", tab="chat", **_child_head(db, child), **_chat_ctx(db, child))


@router.post("/children/{child_id}/chat/clear", dependencies=[Depends(csrf_protect)])
async def child_chat_clear(child_id: int, db: Session = Depends(get_db)):
    """Chat eines Kindes leeren (Datensparsamkeit). Offene Anfragen verfallen mit."""
    child = _get_or_404(db, Child, child_id)
    db.execute(delete(Message).where(Message.child_id == child.id))
    db.commit()
    return _redirect(f"/children/{child.id}/chat?cleared=1")


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
        return render(request, "chat.html", status_code=400, tab="chat", **_child_head(db, child), **_chat_ctx(db, child),
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
        return render(request, "chat.html", status_code=400, tab="chat", **_child_head(db, child), **_chat_ctx(db, child), error=str(e))
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
        return render(request, "chat.html", status_code=400, tab="chat", **_child_head(db, child), **_chat_ctx(db, child), error=str(e))
    await chat.push_to_child(db, msg)
    if msg.status == "approved":
        await policy.push_policies(db, chat.device_ids_for_child(db, child.id))
    return _redirect(f"/children/{child_id}/chat#m{msg.id}")


# ---------- Geräte ----------

@router.get("/devices")
def devices_list(request: Request, db: Session = Depends(get_db)):
    devices = sorted(_by_name(db.scalars(select(Device))), key=lambda d: d.revoked_at is not None)
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
                  base_url=settings.base_url, minutes=settings.pairing_code_minutes,
                  installer=next(iter(versions.windows_installers()), None))


@router.get("/devices/{device_id}")
def device_detail(device_id: int, request: Request, db: Session = Depends(get_db)):
    device = _get_or_404(db, Device, device_id)
    children = _by_name(db.scalars(select(Child)))
    online = manager.is_online(device.id)
    return render(request, "device.html", device=device, children=children,
                  online=online, supported=sorted(SUPPORTED_VERSIONS),
                  policy_current=policy.policy_current(db, device),
                  self_update=selfupdate.supported(device), update_state=selfupdate.state(device),
                  can_update=selfupdate.can_request(device, online))


@router.post("/devices/{device_id}/update", dependencies=[Depends(csrf_protect)])
async def device_update(device_id: int, db: Session = Depends(get_db)):
    """kidCTL auf dem Gerät aktualisieren: nur auslösen – Paket und Quelle bestimmt der Agent."""
    device = _get_or_404(db, Device, device_id)
    if selfupdate.can_request(device, manager.is_online(device_id)):
        selfupdate.mark_requested(device)
        db.commit()
        if not await manager.send(device_id, UpdateNow().model_dump()):
            device.update_state, device.update_requested_at = "", None   # gerade getrennt: nicht als „läuft“ zeigen
            db.commit()
    return _redirect(f"/devices/{device_id}")


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


@router.post("/devices/{device_id}/delete", dependencies=[Depends(csrf_protect)])
async def device_delete(device_id: int, db: Session = Depends(get_db)):
    """Gerät samt Zuordnungen entfernen; Nutzungsstatistik je Kind bleibt (device_id → NULL)."""
    device = _get_or_404(db, Device, device_id)
    db.delete(device)
    db.commit()
    await manager.disconnect(device_id, CLOSE_REVOKED, "Gerät entfernt")
    return _redirect("/devices")


# ---------- Eltern-Accounts ----------

@router.get("/parents")
def parents_list(request: Request, db: Session = Depends(get_db)):
    return render(request, "parents.html", **_parents_ctx(db, request.state.session))


def _parents_ctx(db: Session, sess: ParentSession) -> dict:
    roles = {pid: role for pid, role in db.execute(select(Membership.parent_id, Membership.role))}
    invites = [i for i in db.scalars(select(FamilyInvite).order_by(FamilyInvite.created_at))
               if i.used_at is None and i.expires_at > utcnow()]
    return {"parents": db.scalars(select(Parent).order_by(Parent.username)).all(), "roles": roles,
            "is_owner": roles.get(sess.parent_id) == ROLE_OWNER, "invites": invites}


def _role(db: Session, parent_id: int) -> str | None:
    return db.scalar(select(Membership.role).where(Membership.parent_id == parent_id))


def _require_owner(db: Session, sess: ParentSession) -> None:
    """Eltern einladen/entfernen, Abo, Export und Löschen der Familie: nur der Inhaber."""
    if _role(db, sess.parent_id) != ROLE_OWNER:
        raise HTTPException(403, "Nur der Inhaber der Familie")


@router.post("/parents/invite")
def parent_invite(request: Request, email: str = Form(""), display_name: str = Form(""),
                  sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Inhaber lädt ein weiteres Elternteil ein: Link anzeigen und (mit SMTP) per Mail schicken."""
    _require_owner(db, sess)
    email = email.strip()
    error = (_("Bitte eine E-Mail-Adresse angeben.") if not email else None) or accounts.email_error(email, db)
    if error:
        return render(request, "parents.html", status_code=400, error=error, **_parents_ctx(db, sess))
    raw = accounts.create_invite(db, email, display_name, sess.parent_id)
    link = f"{settings.base_url}/invite/{raw}"
    by = sess.parent.display_name or sess.parent.username
    sent = settings.mail_enabled and accounts.send_invite(email, link, by, i18n.current())
    return render(request, "parent_invited.html", email=email, link=link, sent=sent, days=accounts.INVITE_DAYS)


@router.post("/parents/invites/{invite_id}/delete")
def parent_invite_delete(invite_id: int, sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    _require_owner(db, sess)
    db.delete(_get_or_404(db, FamilyInvite, invite_id))
    db.commit()
    return _redirect("/parents")


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
    removed = passkeys.remove_all(db, "parent", parent.id)   # Entzug: auch Passkeys gelten nicht mehr
    db.commit()
    if removed:
        passkeys.notify_parent(parent, "reset")
    by = sess.parent.display_name or sess.parent.username
    accounts.notify(parent, N_("Authenticator zurückgesetzt"),
                    N_("{by} hat den Authenticator deines Accounts „{username}“ zurückgesetzt. "
                       "Den Einrichtungslink bekommst du von dieser Person."), by=by, username=parent.username)
    return render(request, "totp_reset_link.html", parent=parent, link=f"{settings.base_url}/totp-setup/{raw}",
                  hours=accounts.TOTP_SETUP_HOURS)


@router.post("/parents/{parent_id}/delete")
def parent_delete(parent_id: int, sess: ParentSession = Depends(csrf_protect),
                  db: Session = Depends(get_db)):
    target = _get_or_404(db, Parent, parent_id)   # zuerst: fremde Accounts gibt es hier nicht
    _require_owner(db, sess)
    if parent_id == sess.parent_id:
        raise HTTPException(400, "Eigenen Account nicht löschbar")
    if _role(db, parent_id) == ROLE_OWNER:
        raise HTTPException(400, "Inhaber nicht löschbar")
    passkeys.remove_all(db, "parent", target.id)
    db.delete(target)
    db.commit()
    return _redirect("/parents")


# ---------- Profil ----------

def _profile(request: Request, parent: Parent, status_code: int = 200, **ctx):
    from sqlalchemy.orm import object_session
    sess = request.state.session
    return render(request, "profile.html", status_code=status_code, parent=parent,
                  mail_enabled=settings.mail_enabled, intervals=report.INTERVALS, send_hour=report.SEND_HOUR,
                  passkey_list=passkeys.list_for(object_session(parent), "parent", parent.id),
                  fresh=passkeys.is_fresh(sess), fresh_until=sess.confirmed_at and sess.confirmed_at + passkeys.FRESH,
                  **ctx)


_check_password = accounts.check_password


PROFILE_NOTICES = {"saved": N_("Profil gespeichert."),
                   "email_sent": N_("Bestätigungslink gesendet – bitte das Postfach prüfen."), "password": N_("Passwort geändert. Andere Sitzungen wurden abgemeldet."),
                   "totp": N_("Neuer Authenticator eingerichtet. Andere Sitzungen wurden abgemeldet."),
                   "report": N_("Nutzungsbericht gespeichert."), "report_sent": N_("Nutzungsbericht gesendet."),
                   "confirmed": N_("Bestätigt. Für 5 Minuten kannst du Passwort, Authenticator und Passkeys ändern."),
                   "passkey_added": N_("Passkey angelegt."), "passkey_removed": N_("Passkey entfernt."),
                   "passkey_renamed": N_("Passkey umbenannt.")}


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
    if settings.saas and not username:
        username = parent.username   # im SaaS kein eigenes Feld: Anmeldung per E-Mail
    error = accounts.username_error(db, username, parent.id) or accounts.email_error(email, db, parent.id)
    sensitive = username != parent.username or email.lower() != parent.email.lower()
    if not error and sensitive:
        error = _check_password(db, request, parent, current_password)
    if error:
        return _profile(request, parent, 400, error=error, form={"username": username, "display_name": display_name,
                                                                   "email": email, "language": language})
    old_email = parent.email
    parent.username, parent.display_name = username, display_name.strip()[:128]
    parent.language = language
    accounts.set_email(db, parent, email)   # neue Adresse: Bestätigungslink (mit SMTP)
    db.commit()
    if old_email and old_email != email:
        accounts.mailer.send(old_email, "email_changed", i18n.parent_language(parent), username=username, email=email)
    return _redirect("/profile?msg=saved")


@router.post("/profile/email/verify")
def profile_email_verify(request: Request, sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Bestätigungslink erneut senden."""
    parent = db.get(Parent, sess.parent_id)
    if not parent.email or parent.email_verified_at is not None or not settings.mail_enabled:
        return _redirect("/profile")
    if not accounts.send_email_verification(db, parent):
        return _profile(request, parent, 502, error=_("Versand fehlgeschlagen (Details im Server-Log)."))
    return _redirect("/profile?msg=email_sent")


@router.post("/profile/report")
def profile_report(request: Request, interval: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                   db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    if interval not in report.INTERVALS:
        return _profile(request, parent, 400, error=_("Ungültiger Zeitraum."))
    if interval and not accounts.email_usable(parent):
        return _profile(request, parent, 400, error=_("Für den Nutzungsbericht zuerst eine E-Mail-Adresse eintragen und bestätigen."))
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
    from .. import mailer
    nonce = secrets.token_urlsafe(16)   # Mail-HTML mit Inline-Styles: als <style> mit Nonce (CSP ohne unsafe-inline)
    html = mailer.csp_safe(mailer.preview_html(report.render(db, parent, _report_period(parent, interval))[2]), nonce)
    return HTMLResponse(html, headers={"Content-Security-Policy": csp(nonce)})


@router.post("/profile/password")
def profile_password(request: Request, current_password: str = Form(""), current_totp: str = Form(""),
                     password: str = Form(""), repeat: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                     db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    error = accounts.password_error(password, repeat) or \
        passkeys.confirm(db, request, sess, parent, current_password, current_totp, parent.username)
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
                  qr_svg=totp_qr_svg(uri), minutes=accounts.TOTP_CHANGE_MINUTES, error=error,
                  fresh=passkeys.is_fresh(request.state.session))


@router.post("/profile/totp/confirm")
def profile_totp_confirm(request: Request, token: str = Form(""), current_password: str = Form(""),
                         current_totp: str = Form(""), totp: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                         db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    t = accounts.get_token(db, token, "totp_change")
    if t is None or t.parent_id != parent.id:
        return _profile(request, parent, 400, error=_("Die Einrichtung ist abgelaufen. Bitte neu starten."))
    error, step = ps.new_totp_step(
        passkeys.confirm(db, request, sess, parent, current_password, current_totp, parent.username), t.secret, totp)
    if error:
        return _totp_change_page(request, parent, token, t.secret, 400, error)
    parent.totp_secret, parent.totp_last_step = t.secret, step
    t.used_at = utcnow()
    accounts.end_sessions(db, parent.id, keep_session_id=sess.id)
    db.commit()
    accounts.notify(parent, N_("Authenticator geändert"), N_("für deinen Account wurde ein neuer Authenticator eingerichtet."))
    return _redirect("/profile?msg=totp")


# ---------- Passkeys und frische Bestätigung ----------

@router.post("/profile/confirm")
def profile_confirm(request: Request, current_password: str = Form(""), current_totp: str = Form(""),
                    sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    """Frische Bestätigung per Passwort + Code (5 Minuten)."""
    parent = db.get(Parent, sess.parent_id)
    if error := passkeys.confirm(db, request, sess, parent, current_password, current_totp, parent.username):
        return _profile(request, parent, 400, error=error)
    return _redirect("/profile?msg=confirmed#passkeys")


@router.post("/profile/confirm/passkey/options")
def profile_confirm_passkey_options(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    return ps.confirm_passkey_options(db, "parent", sess.parent_id)


@router.post("/profile/confirm/passkey")
def profile_confirm_passkey(request: Request, credential: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                            db: Session = Depends(get_db)):
    """Frische Bestätigung mit einem Passkey dieses Kontos."""
    return ps.confirm_passkey(db, request, sess, "parent", sess.parent_id, credential, "/profile?msg=confirmed#passkeys")


@router.post("/profile/passkeys/options")
def profile_passkey_options(sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    if not passkeys.is_fresh(sess):
        return ps.not_fresh()
    parent = db.get(Parent, sess.parent_id)
    return ps.registration_options(db, "parent", parent.id, parent.email or parent.username,
                                   parent.display_name or parent.username)


@router.post("/profile/passkeys")
def profile_passkey_add(request: Request, credential: str = Form(""), name: str = Form(""),
                        sess: ParentSession = Depends(csrf_protect), db: Session = Depends(get_db)):
    if not passkeys.is_fresh(sess):
        return ps.not_fresh()
    parent = db.get(Parent, sess.parent_id)
    pk, error = ps.register_passkey(db, request, "parent", parent.id, credential, name)
    if error:
        return error
    passkeys.notify_parent(parent, "added", pk.name)
    return ps.json_response({"redirect": "/profile?msg=passkey_added#passkeys"})


@router.post("/profile/passkeys/{passkey_id}/rename")
def profile_passkey_rename(passkey_id: int, name: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                           db: Session = Depends(get_db)):
    ps.rename_passkey(db, "parent", sess.parent_id, passkey_id, name)
    return _redirect("/profile?msg=passkey_renamed#passkeys")


@router.post("/profile/passkeys/{passkey_id}/delete")
def profile_passkey_delete(passkey_id: int, request: Request, current_password: str = Form(""),
                           current_totp: str = Form(""), sess: ParentSession = Depends(csrf_protect),
                           db: Session = Depends(get_db)):
    parent = db.get(Parent, sess.parent_id)
    pk = ps.own_passkey_or_404(db, "parent", parent.id, passkey_id)
    if error := passkeys.confirm(db, request, sess, parent, current_password, current_totp, parent.username):
        return _profile(request, parent, 400, error=error)
    name = pk.name
    db.delete(pk)
    db.commit()
    passkeys.notify_parent(parent, "removed", name)
    return _redirect("/profile?msg=passkey_removed#passkeys")
