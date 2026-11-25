"""Policy-Berechnung/-Verteilung an Agenten und geräteübergreifende Nutzungszeit."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from . import access
from . import applimits
from . import lock as lk
from . import schedule as sch
from . import timebank
from . import vacations as vac
from .chat import device_ids_for_child
from .programs import free_keys, game_keys, policy_programs
from .webfilter import web_policy
from .config import settings
from .tenant import family_session, platform_session
from .models import Child, ChildBlocklist, DayException, Device, DeviceUser, UsageRecord, utcnow
from .ws import manager

log = logging.getLogger("kidctl.policy")

# Meldungen außerhalb dieses Bereichs (relativ zu heute) werden ignoriert (falsche Uhr o. Ä.)
USAGE_DAYS_BACK = 7


# ---------- Policy ----------

def build_policy(db: Session, device_id: int) -> p.Policy:
    from .models import Family
    family_id, proto = db.execute(select(Device.family_id, Device.protocol_version)
                                  .where(Device.id == device_id)).one_or_none() or (None, None)
    family = db.get(Family, family_id) if family_id is not None else None
    if family is not None and family.paused_at is not None:
        return paused_policy(db, device_id)
    mapped = db.execute(select(DeviceUser.username, DeviceUser.child_id).where(
        DeviceUser.device_id == device_id, DeviceUser.child_id.is_not(None))
        .order_by(DeviceUser.username)).all()
    # Ungültige Namen (z. B. von älteren Agenten gemeldet) nie verteilen – das Protokoll lehnt sie ab
    users = {u: c for u, c in mapped if re.fullmatch(p.USERNAME_PATTERN, u)}
    today = sch.today_local()
    since = today - timedelta(days=1)  # Puffer für Uhr-/Zeitzonenränder
    cal = vac.Calendar.load(db)
    children = []
    for child in db.scalars(select(Child).where(Child.id.in_(set(users.values()))).order_by(Child.id)):
        excs = db.scalars(select(DayException).where(
            DayException.child_id == child.id, DayException.day >= since).order_by(DayException.day))
        # Zeitkonto: heute nutzbares Guthaben als zusätzliche Tagesausnahme (Agent addiert alle Ausnahmen eines Tages)
        bank = timebank.for_day(db, child, today, cal).available
        children.append(p.ChildPolicy(
            id=child.id, name=child.name,
            # Geltende Regeln von heute … heute+6 (Ferien/Feiertage eingerechnet) – siehe app.vacations
            rules=[p.DayRule(weekday=wd, quota_minutes=r.quota_minutes,
                             window_start=r.window_start, window_end=r.window_end,
                             game_minutes=r.game_minutes, blocks=r.block_list) for wd, r in cal.week_rules(child, today)],
            exceptions=[p.DayOverride(day=e.day, extra_minutes=e.extra_minutes,
                                      window_end_override=e.window_end_override,
                                      extra_game_minutes=e.extra_game_minutes or 0) for e in excs]
                       + ([p.DayOverride(day=today, extra_minutes=bank)] if bank else []),
            program_mode=child.program_mode,
            allowed_programs=policy_programs(db, child.id),
            game_apps=game_keys(db, child.id),
            free_apps=free_keys(db, child.id),
            app_limits=applimits.policy_limits(db, child.id),
            web=web_policy(db, child),
            locked=lk.is_locked(child),
            lock_grace_seconds=(child.lock_grace_minutes if child.lock_grace_minutes is not None else 1) * 60,
            locked_until=child.locked_until.replace(tzinfo=timezone.utc) if lk.is_locked(child) and child.locked_until else None,
            temp_grants=access.temp_grants(db, child.id),
        ))
    body = p.Policy(version="", timezone=settings.timezone, users=users, children=children)
    if proto is not None and proto < 2:
        body = downgrade_v1(body)
    digest = hashlib.sha256(body.model_dump_json(exclude={"version"}).encode()).hexdigest()[:16]
    return body.model_copy(update={"version": digest})


V1_LATEST_END = time(23, 59)


def downgrade_v1(pol: p.Policy) -> p.Policy:
    """Policy für Agenten mit Protokoll 1: ohne Sperrzeiten, Fensterende über Mitternacht auf 23:59 gekappt (ein v1-
    Agent würde ein Ende vor dem Beginn als „ganzer Tag gesperrt“ lesen)."""
    children = []
    for c in pol.children:
        starts = {r.weekday: r.window_start for r in c.rules}
        rules = [r.model_copy(update={"blocks": [],
                                      "window_end": V1_LATEST_END if r.window_end < r.window_start else r.window_end})
                 for r in c.rules]
        excs = [e.model_copy(update={"window_end_override": V1_LATEST_END})
                if e.window_end_override is not None and e.window_end_override < starts.get(e.day.weekday(), time(0))
                else e for e in c.exceptions]
        children.append(c.model_copy(update={"rules": rules, "exceptions": excs}))
    return pol.model_copy(update={"children": children})


def needs_v2(child: Child) -> bool:
    """Nutzt das Kind Sperrzeiten oder ein Fenster über Mitternacht (kann ein Agent mit Protokoll 1 nicht)?"""
    return any(r.blocks or r.window_end < r.window_start for r in child.rules)


def old_protocol_devices(db: Session, child: Child) -> list[str]:
    """Namen der Geräte des Kindes mit Protokoll 1 – nur, wenn das Kind Funktionen ab Protokoll 2 nutzt."""
    if not needs_v2(child):
        return []
    ids = device_ids_for_child(db, child.id)
    return sorted(d.name for d in db.scalars(select(Device).where(Device.id.in_(ids)))
                  if 0 < (d.protocol_version or 0) < 2)


def paused_policy(db: Session, device_id: int) -> p.Policy:
    """Ruhemodus: keine Zuordnungen und keine Kinder – auch ältere Agenten heben damit alle Einschränkungen auf.
    ``paused_users`` nennt die sonst zugeordneten User (Tray zeigt „KidCTL pausiert“)."""
    names = db.scalars(select(DeviceUser.username).where(DeviceUser.device_id == device_id,
                                                         DeviceUser.child_id.is_not(None)))
    paused = sorted(u for u in names if re.fullmatch(p.USERNAME_PATTERN, u))
    body = p.Policy(version="", timezone=settings.timezone, users={}, children=[], paused=True, paused_users=paused)
    digest = hashlib.sha256(body.model_dump_json(exclude={"version"}).encode()).hexdigest()[:16]
    return body.model_copy(update={"version": digest})


async def send_policy(db: Session, device_id: int) -> None:
    """Sendet Policy + aktuellen Nutzungsstand, falls das Gerät online ist."""
    if not manager.is_online(device_id):
        return
    pol = build_policy(db, device_id)
    try:
        await manager.send(device_id, pol.model_dump(mode="json"))
        today = sch.today_local()
        for child in pol.children:
            await manager.send(device_id, usage_update(db, device_id, child.id, today).model_dump(mode="json"))
    except Exception:
        log.warning("Policy an Gerät %s nicht zustellbar", device_id, exc_info=True)


async def push_policies(db: Session, device_ids: set[int]) -> None:
    for device_id in sorted(device_ids):
        await send_policy(db, device_id)


def policy_current(db: Session, device: Device) -> bool:
    return bool(device.policy_version) and device.policy_version == build_policy(db, device.id).version


# ---------- Nutzungszeit ----------

def record_usage(db: Session, device_id: int, report: p.UsageReport) -> set[tuple[int, date]]:
    """Übernimmt absolute Zählerstände (Maximum). Gibt geänderte (Kind, Tag)-Paare zurück."""
    today = sch.today_local()
    mapping = dict(db.execute(select(DeviceUser.username, DeviceUser.child_id).where(
        DeviceUser.device_id == device_id)).all())
    changed: set[tuple[int, date]] = set()
    entries = [e for e in report.entries
               if today - timedelta(days=USAGE_DAYS_BACK) <= e.day <= today + timedelta(days=1)]
    existing = {(r.username, r.day): r for r in db.scalars(select(UsageRecord).where(
        UsageRecord.device_id == device_id, UsageRecord.day.in_({e.day for e in entries})))} if entries else {}
    for e in entries:
        rec = existing.get((e.username, e.day))
        if rec is None:
            child_id = mapping.get(e.username)
            if child_id is None:
                continue  # nicht zugeordnete User werden nicht erfasst
            rec = UsageRecord(device_id=device_id, username=e.username, day=e.day,
                              child_id=child_id, seconds=0)
            db.add(rec)
            existing[(e.username, e.day)] = rec
        limits_changed = applimits.merge(rec, e.limit_seconds)
        if e.seconds > rec.seconds or e.game_seconds > (rec.game_seconds or 0) or limits_changed:
            rec.seconds, rec.updated_at = max(rec.seconds, e.seconds), utcnow()
            rec.game_seconds = max(rec.game_seconds or 0, e.game_seconds)
            if rec.child_id is not None:
                changed.add((rec.child_id, e.day))
    db.commit()
    return changed


def others_seconds(db: Session, device_id: int, child_id: int, day: date, games: bool = False) -> int:
    col = UsageRecord.game_seconds if games else UsageRecord.seconds
    return db.scalar(select(func.coalesce(func.sum(col), 0)).where(
        UsageRecord.child_id == child_id, UsageRecord.day == day,
        UsageRecord.device_id.is_distinct_from(device_id))) or 0


def usage_update(db: Session, device_id: int, child_id: int, day: date) -> p.UsageUpdate:
    """Nutzung (und Spielzeit) des Kindes an ``day`` auf allen *anderen* Geräten."""
    return p.UsageUpdate(child_id=child_id, day=day, others_seconds=others_seconds(db, device_id, child_id, day),
                         others_game_seconds=others_seconds(db, device_id, child_id, day, games=True),
                         others_limit_seconds=applimits.others(db, device_id, child_id, day))


def usage_update_payloads(db: Session, changed: set[tuple[int, date]], source_device: int) -> list[tuple[int, dict]]:
    """(Gerät, Nachricht) für die *anderen* online-Geräte eines Kindes – ohne zu senden (auch aus Worker-Threads)."""
    out = []
    for child_id, day in changed:
        for device_id in device_ids_for_child(db, child_id) - {source_device}:
            if manager.is_online(device_id):
                out.append((device_id, usage_update(db, device_id, child_id, day).model_dump(mode="json")))
    return out


def bank_policy_payloads(db: Session, changed: set[tuple[int, date]]) -> list[tuple[int, dict]]:
    """Nachträgliche Nutzung vergangener Tage ändert das Zeitkonto: geänderte Policies für die online-Geräte der
    betroffenen Kinder (auch das meldende Gerät) – ohne zu senden."""
    today = sch.today_local()
    out = []
    for child_id in sorted({c for c, d in changed if d < today}):
        child = db.get(Child, child_id)
        if child is None or not child.bank_enabled:
            continue
        for device_id in sorted(device_ids_for_child(db, child_id)):
            dev = db.get(Device, device_id)
            if dev is None or not manager.is_online(device_id):
                continue
            pol = build_policy(db, device_id)
            if pol.version != dev.policy_version:
                out.append((device_id, pol.model_dump(mode="json")))
    return out


async def push_usage_updates(db: Session, changed: set[tuple[int, date]], source_device: int) -> None:
    """Informiert die *anderen* online-Geräte eines Kindes über neue Nutzungszeit."""
    for device_id, payload in usage_update_payloads(db, changed, source_device):
        await manager.send(device_id, payload)


@dataclass
class DayStatus:
    used_seconds: int
    allowed_minutes: int            # inkl. Gutschrift und Zeitkonto
    window_start: object
    window_end: object
    bank_minutes: int = 0           # davon aus dem Zeitkonto
    bank_balance: int = 0           # Kontostand zu Tagesbeginn

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.allowed_minutes - self.used_seconds // 60)

    @property
    def overrun_minutes(self) -> int:
        """Überziehung (z. B. durch gleichzeitige Offline-Nutzung) – nur Anzeige."""
        return max(0, self.used_seconds // 60 - self.allowed_minutes)


def day_status(db: Session, child: Child, day: date | None = None, cal: vac.Calendar | None = None,
               bank: timebank.BankDay | None = None) -> DayStatus:
    """Stand eines Tages; ``bank`` vorab berechnet (``timebank.simulate``) spart bei Tageslisten die Einzelrechnung."""
    day = day or sch.today_local()
    cal = cal or vac.Calendar.load(db)
    rule = cal.rule_for(child, day)
    if bank is None:
        bank = timebank.for_day(db, child, day, cal)
    exc = db.scalar(select(DayException).where(DayException.child_id == child.id, DayException.day == day))
    used = db.scalar(select(func.coalesce(func.sum(UsageRecord.seconds), 0)).where(
        UsageRecord.child_id == child.id, UsageRecord.day == day)) or 0
    quota = (rule.quota_minutes if rule else 0) + (exc.extra_minutes if exc else 0) + bank.available
    end = exc.window_end_override if exc and exc.window_end_override else (rule.window_end if rule else None)
    return DayStatus(used_seconds=used, allowed_minutes=quota,
                     window_start=rule.window_start if rule else None, window_end=end,
                     bank_minutes=bank.available, bank_balance=bank.balance)


async def push_for_lists(list_ids: set[str]) -> None:
    """Neue Sperrlisten-Versionen: Policies der Geräte betroffener Kinder neu senden."""
    with platform_session() as db:   # Listen gelten über alle Familien; Policies je Familie
        families = set(db.scalars(select(ChildBlocklist.family_id).where(ChildBlocklist.list_id.in_(list_ids))))
    for fid in sorted(families):
        with family_session(fid) as db:
            children = set(db.scalars(select(ChildBlocklist.child_id).where(ChildBlocklist.list_id.in_(list_ids))))
            devices = set()
            for cid in children:
                devices |= device_ids_for_child(db, cid)
            await push_policies(db, devices)
