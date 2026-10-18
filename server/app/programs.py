"""Programm-Whitelist: App-Katalog der Geräte, Freigaben je Kind, Sperrprotokoll."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from .chat import device_ids_for_child
from .models import BlockedEvent, ChildCustomProgram, ChildProgram, Device, DeviceApp, DeviceUser

MODES = {"off": "Aus", "audit": "Nur protokollieren", "enforce": "Sperren"}
MAX_EVENTS_PER_REPORT = 500
LOG_LIMIT = 50


def apply_app_report(db: Session, dev: Device, msg: p.ReportApps) -> None:
    """Katalog des Geräts vollständig ersetzen (der Agent meldet immer alles)."""
    db.execute(delete(DeviceApp).where(DeviceApp.device_id == dev.id))
    seen = set()
    for a in msg.apps:
        if a.key in seen:
            continue
        seen.add(a.key)
        db.add(DeviceApp(device_id=dev.id, key=a.key, name=a.name, icon=a.icon, desktop_id=a.desktop_id,
                         categories=";".join(a.categories)))


def apply_blocked_report(db: Session, dev: Device, msg: p.BlockedReport) -> int:
    mapping = dict(db.execute(select(DeviceUser.username, DeviceUser.child_id)
                              .where(DeviceUser.device_id == dev.id)).all())
    n = 0
    for e in msg.events[:MAX_EVENTS_PER_REPORT]:
        child_id = mapping.get(e.username)
        if child_id is None:
            continue  # nicht zugeordnete User werden nicht protokolliert
        db.add(BlockedEvent(child_id=child_id, device_id=dev.id, username=e.username, at=_utc_naive(e.at),
                            kind=e.kind, key=e.key, name=e.name, detail=e.detail, audit=e.audit))
        n += 1
    return n


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@dataclass
class CatalogEntry:
    key: str
    name: str
    devices: int        # auf wie vielen Geräten des Kindes installiert
    allowed: bool
    missing: bool = False  # freigegeben, aber auf keinem Gerät (mehr) gemeldet
    group: str = "other"
    custom_id: int | None = None  # eigenes Programm der Eltern (löschbar)


# Gruppen der Programmliste: (ID, Anzeige, freedesktop-Kategorien) – erste passende Gruppe gewinnt
# (Spiele vor Lernen usw., weil viele Apps mehrere Kategorien tragen).
GROUPS = [
    ("game", "Spiele", {"Game"}),
    ("education", "Lernen", {"Education", "Science"}),
    ("internet", "Internet & Kommunikation", {"Network", "WebBrowser", "Email", "Chat", "InstantMessaging"}),
    ("office", "Büro", {"Office"}),
    ("graphics", "Grafik", {"Graphics"}),
    ("media", "Musik & Video", {"AudioVideo", "Audio", "Video"}),
    ("development", "Entwicklung", {"Development"}),
    ("terminal", "Terminal & Shells", {"TerminalEmulator", "Shell"}),
    ("utility", "Werkzeuge", {"Utility"}),
    ("system", "System & Einstellungen", {"System", "Settings", "Monitor", "PackageManager"}),
]
GROUP_LABELS = {gid: label for gid, label, _ in GROUPS} | {
    "custom": "Eigene Programme", "other": "Sonstiges", "missing": "Nicht mehr installiert"}
COLLAPSED_GROUPS = {"system"}  # standardmäßig eingeklappt


def group_for(categories: str) -> str:
    cats = {c for c in categories.split(";") if c}
    return next((gid for gid, _label, match in GROUPS if cats & match), "other")


def grouped_catalog(entries: list[CatalogEntry]) -> list[tuple[str, str, list[CatalogEntry]]]:
    """(ID, Anzeige, Einträge) in fester Reihenfolge, leere Gruppen weggelassen."""
    order = ["custom"] + [gid for gid, _l, _c in GROUPS] + ["other", "missing"]
    by_group: dict[str, list[CatalogEntry]] = {}
    for e in entries:
        by_group.setdefault("custom" if e.custom_id else "missing" if e.missing else e.group, []).append(e)
    return [(gid, GROUP_LABELS[gid], sorted(by_group[gid], key=lambda e: e.name.lower()))
            for gid in order if gid in by_group]


def catalog_for_child(db: Session, child_id: int) -> list[CatalogEntry]:
    devices = device_ids_for_child(db, child_id)
    allowed = set(allowed_keys(db, child_id))
    entries: dict[str, CatalogEntry] = {}
    if devices:
        for app in db.scalars(select(DeviceApp).where(DeviceApp.device_id.in_(devices))):
            e = entries.get(app.key)
            if e is None:
                entries[app.key] = CatalogEntry(app.key, app.name, 1, app.key in allowed,
                                                group=group_for(app.categories or ""))
            else:
                e.devices += 1
    for c in custom_programs(db, child_id):  # eigene Programme: Name der Eltern, eigene Gruppe
        e = entries.get(c.key)
        entries[c.key] = CatalogEntry(c.key, c.name, e.devices if e else 0, c.key in allowed, custom_id=c.id)
    for key in allowed - entries.keys():
        entries[key] = CatalogEntry(key, key.split(":", 1)[-1].rsplit("/", 1)[-1], 0, True, missing=True)
    return sorted(entries.values(), key=lambda e: (not e.allowed, e.name.lower()))


def allowed_keys(db: Session, child_id: int) -> list[str]:
    return sorted(db.scalars(select(ChildProgram.key).where(ChildProgram.child_id == child_id)))


def set_allowed(db: Session, child_id: int, keys: set[str]) -> None:
    current = set(allowed_keys(db, child_id))
    for key in current - keys:
        db.execute(delete(ChildProgram).where(ChildProgram.child_id == child_id, ChildProgram.key == key))
    for key in keys - current:
        db.add(ChildProgram(child_id=child_id, key=key))


# ---------- Eigene Programme ----------

MAX_CUSTOM_NAME = 64
_PATH_RE = re.compile(r"^/[A-Za-z0-9._+@/-]{1,500}$")


def custom_key(path: str) -> str:
    """Eingabe der Eltern -> App-Schlüssel ``exe:<pfad>``. Ohne ``/`` wird ``/usr/bin/<name>``
    angenommen (Arch: alle Programme dort). ``ValueError`` bei ungültiger Eingabe."""
    path = path.strip()
    if path and "/" not in path:
        path = "/usr/bin/" + path
    if not _PATH_RE.match(path) or posixpath.normpath(path) != path or path.endswith("/"):
        raise ValueError("Bitte einen absoluten Programmpfad angeben, z. B. /usr/bin/git")
    return "exe:" + path


def custom_programs(db: Session, child_id: int) -> list[ChildCustomProgram]:
    return list(db.scalars(select(ChildCustomProgram).where(ChildCustomProgram.child_id == child_id)
                           .order_by(ChildCustomProgram.name)))


def add_custom(db: Session, child_id: int, path: str, name: str = "") -> ChildCustomProgram:
    """Eigenes Programm eintragen und gleich freigeben (vorhandener Eintrag wird aktualisiert)."""
    key = custom_key(path)
    name = (name.strip() or key.rsplit("/", 1)[-1])[:MAX_CUSTOM_NAME]
    entry = db.scalar(select(ChildCustomProgram).where(ChildCustomProgram.child_id == child_id,
                                                       ChildCustomProgram.key == key))
    if entry is None:
        entry = ChildCustomProgram(child_id=child_id, key=key, name=name)
        db.add(entry)
    else:
        entry.name = name
    set_allowed(db, child_id, set(allowed_keys(db, child_id)) | {key})
    return entry


def delete_custom(db: Session, child_id: int, custom_id: int) -> bool:
    """Eintrag löschen und Freigabe entziehen."""
    entry = db.get(ChildCustomProgram, custom_id)
    if entry is None or entry.child_id != child_id:
        return False
    db.execute(delete(ChildProgram).where(ChildProgram.child_id == child_id, ChildProgram.key == entry.key))
    db.delete(entry)
    return True


def blocked_log(db: Session, child_id: int, limit: int = LOG_LIMIT) -> list[BlockedEvent]:
    return list(db.scalars(select(BlockedEvent).where(BlockedEvent.child_id == child_id)
                           .order_by(BlockedEvent.at.desc(), BlockedEvent.id.desc()).limit(limit)))
