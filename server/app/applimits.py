"""Limit je Programm/Kategorie (K4): Einstellungen je Kind (``child_app_rule.limit_minutes``), Policy-Einträge und
Zeit je Limit (``usage_record.limit_seconds``, geräteübergreifend wie die Spielzeit)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kidctl_common import applimits as al
from kidctl_common import protocol as p

from .i18n import _
from .models import ChildAppRule, ChildCustomProgram, DeviceApp, UsageRecord


class LimitError(ValueError):
    pass


@dataclass
class LimitRow:
    key: str
    name: str
    minutes: int
    used_seconds: int = 0

    @property
    def used_minutes(self) -> int:
        return self.used_seconds // 60


def rules(db: Session, child_id: int) -> list[ChildAppRule]:
    return list(db.scalars(select(ChildAppRule).where(ChildAppRule.child_id == child_id,
                                                      ChildAppRule.limit_minutes.is_not(None))))


def _names(db: Session, child_id: int, localized: bool = True) -> dict[str, str]:
    """Programmnamen (Katalog der Geräte, eigene Programme mit dem Namen der Eltern). ``localized=False``: immer der
    Hauptname – die Policy darf nicht von der Sprache der Anfrage abhängen (Versions-Hash)."""
    names = {a.key: a.label if localized else a.name for a in db.scalars(select(DeviceApp))}
    names |= {c.key: c.name for c in db.scalars(select(ChildCustomProgram).where(ChildCustomProgram.child_id == child_id))}
    names[p.STEAM_KEY] = "Steam"
    return names


def name_for(key: str, names: dict[str, str]) -> str:
    if al.is_category(key):
        return _(al.category_label(key))
    return names.get(key) or key.split(":", 1)[-1].replace("\\", "/").rsplit("/", 1)[-1]


def limit_rows(db: Session, child_id: int, day: date | None = None) -> list[LimitRow]:
    """Limits des Kindes (Kategorien zuerst, dann nach Name), mit Nutzung an ``day``."""
    names = _names(db, child_id)
    used = used_on(db, child_id, day) if day else {}
    out = [LimitRow(r.key, name_for(r.key, names), r.limit_minutes, used.get(r.key, 0)) for r in rules(db, child_id)]
    return sorted(out, key=lambda r: (not al.is_category(r.key), r.name.lower()))


def policy_limits(db: Session, child_id: int) -> list[p.AppLimit]:
    names = _names(db, child_id, localized=False)
    return [p.AppLimit(key=r.key, minutes=r.limit_minutes, name="" if al.is_category(r.key) else name_for(r.key, names)[:255])
            for r in sorted(rules(db, child_id), key=lambda r: r.key)]


def set_limit(db: Session, child_id: int, key: str, minutes_raw: str, allowed_keys: set[str]) -> None:
    """Limit setzen bzw. ändern. ``allowed_keys`` = wählbare Programme (Katalog des Kindes)."""
    key = key.strip()[:512]
    if not (al.is_category(key) or key in allowed_keys):
        raise LimitError(_("Bitte ein Programm oder eine Kategorie auswählen."))
    try:
        minutes = int(minutes_raw)
    except (TypeError, ValueError):
        raise LimitError(_("Bitte die Minuten pro Tag angeben."))
    if not 0 <= minutes <= al.MAX_MINUTES:
        raise LimitError(_("Das Limit muss zwischen 0 und {max} Minuten liegen.").format(max=al.MAX_MINUTES))
    rule = db.scalar(select(ChildAppRule).where(ChildAppRule.child_id == child_id, ChildAppRule.key == key))
    if (rule is None or rule.limit_minutes is None) and len(rules(db, child_id)) >= al.MAX_LIMITS:
        raise LimitError(_("Höchstens {n} Limits je Kind.").format(n=al.MAX_LIMITS))
    if rule is None:
        db.add(ChildAppRule(child_id=child_id, key=key, free=False, limit_minutes=minutes))
    else:
        rule.limit_minutes = minutes


def remove_limit(db: Session, child_id: int, key: str) -> None:
    rule = db.scalar(select(ChildAppRule).where(ChildAppRule.child_id == child_id, ChildAppRule.key == key))
    if rule is None:
        return
    if rule.free:
        rule.limit_minutes = None
    else:
        db.execute(delete(ChildAppRule).where(ChildAppRule.id == rule.id))


def old_devices(devices) -> list[str]:
    """Geräte, deren Agent Limits je Programm noch nicht kennt (dort gilt nur die Bildschirmzeit)."""
    return sorted(d.name for d in devices if (d.protocol_version or 0) > 0
                  and p.FEATURE_APP_LIMITS not in (d.features or "").split(","))


# ---------- Zeit je Limit ----------

def parse(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)} if isinstance(data, dict) else {}


def merge(rec: UsageRecord, reported: dict[str, int]) -> bool:
    """Gemeldete Zählerstände übernehmen (je Limit das Maximum). True = geändert."""
    if not reported:
        return False
    current = parse(rec.limit_seconds)
    merged = dict(current)
    for key, sec in reported.items():
        merged[key] = max(merged.get(key, 0), sec)
    if merged == current:
        return False
    rec.limit_seconds = json.dumps(merged, sort_keys=True)
    return True


def _sum(records) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw in records:
        for key, sec in parse(raw).items():
            out[key] = out.get(key, 0) + sec
    return out


def used_on(db: Session, child_id: int, day: date) -> dict[str, int]:
    return _sum(db.scalars(select(UsageRecord.limit_seconds).where(
        UsageRecord.child_id == child_id, UsageRecord.day == day, UsageRecord.limit_seconds.is_not(None))))


def others(db: Session, device_id: int, child_id: int, day: date) -> dict[str, int]:
    return _sum(db.scalars(select(UsageRecord.limit_seconds).where(
        UsageRecord.child_id == child_id, UsageRecord.day == day, UsageRecord.limit_seconds.is_not(None),
        UsageRecord.device_id.is_distinct_from(device_id))))
