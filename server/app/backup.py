"""Datenbank-Backup: täglich ab ``BACKUP_HOUR`` Uhr ein konsistenter SQLite-Schnappschuss (Backup-API, auch
während der Server schreibt), geprüft (``integrity_check``), gzip-komprimiert nach ``<data>/backups``. Die
neuesten ``KEEP`` bleiben. Alarm per Push, wenn das letzte Backup älter als ``ALERT_AFTER`` ist oder ein
Versuch fehlschlägt – einmal je Störung, erneut erst nach einem erfolgreichen Backup.

Das externe Backup des Hosts sichert ``./data`` und damit auch diese Schnappschüsse.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import push
from .config import settings
from .db import engine
from .i18n import _

log = logging.getLogger("kidctl.backup")

BACKUP_HOUR = 3
KEEP = 14
ALERT_AFTER = timedelta(days=2)
CHECK_SECONDS = 600
PREFIX, SUFFIX = "kidctl-", ".db.gz"
STATE_FILE = "state.json"


def backup_dir() -> Path:
    d = Path(settings.data_dir) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class BackupInfo:
    path: Path
    at: datetime        # lokale Zeit
    size: int           # Bytes


def backups() -> list[BackupInfo]:
    """Vorhandene Schnappschüsse, neueste zuerst."""
    tz = ZoneInfo(settings.timezone)
    out = []
    for f in backup_dir().glob(f"{PREFIX}*{SUFFIX}"):
        st = f.stat()
        out.append(BackupInfo(f, datetime.fromtimestamp(st.st_mtime, tz), st.st_size))
    return sorted(out, key=lambda b: b.at, reverse=True)


def find(name: str) -> BackupInfo | None:
    """Backup nach Dateiname (nur vorhandene Schnappschüsse – kein Pfad von außen)."""
    return next((b for b in backups() if b.path.name == name), None)


def latest() -> BackupInfo | None:
    found = backups()
    return found[0] if found else None


def _state() -> dict:
    try:
        return json.loads((backup_dir() / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    (backup_dir() / STATE_FILE).write_text(json.dumps(state))


def last_error() -> str:
    return str(_state().get("error", ""))


def run(now: datetime | None = None) -> BackupInfo:
    """Schnappschuss erstellen und alte entfernen. Wirft bei Fehlern (Fehler wird vermerkt)."""
    now = now or datetime.now(ZoneInfo(settings.timezone))
    target = backup_dir() / f"{PREFIX}{now:%Y-%m-%d-%H%M}{SUFFIX}"
    try:
        with tempfile.TemporaryDirectory(dir=backup_dir()) as tmp:
            copy = Path(tmp) / "snapshot.db"
            src = sqlite3.connect(engine.url.database)
            dst = sqlite3.connect(copy)
            try:
                src.backup(dst)
                ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                dst.close()
                src.close()
            if ok != "ok":
                raise RuntimeError(f"integrity_check: {ok}")
            packed = Path(tmp) / "snapshot.db.gz"
            with copy.open("rb") as fin, gzip.open(packed, "wb", compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout)
            os.chmod(packed, 0o600)
            os.replace(packed, target)
    except Exception as e:
        state = _state()
        state["error"] = f"{now:%Y-%m-%d %H:%M}: {e}"
        _save_state(state)
        raise
    for old in backups()[KEEP:]:
        old.path.unlink(missing_ok=True)
    _save_state({})   # Fehler und Alarm zurücksetzen
    info = latest()
    log.info("Backup erstellt: %s (%d Bytes)", info.path.name, info.size)
    return info


def due(now: datetime) -> bool:
    """Heute ab BACKUP_HOUR noch kein Backup."""
    if now.hour < BACKUP_HOUR:
        return False
    info = latest()
    return info is None or info.at.date() < now.date()


def check_alarm(now: datetime) -> bool:
    """Push, wenn das letzte Backup zu alt ist oder fehlschlug – einmal je Störung. True = alarmiert."""
    state = _state()
    info = latest()
    stale = info is None or now - info.at > ALERT_AFTER
    if not (stale or state.get("error")) or state.get("alerted"):
        return False
    if info is None and not state.get("error") and now.hour < BACKUP_HOUR + 1:
        return False   # frische Installation: erst den ersten Versuch abwarten
    error = state.get("error", "")
    push.send_to_parents_sync(lambda: _("Datenbank-Backup fehlgeschlagen") if error else _("Datenbank-Backup veraltet"),
                              lambda: error[:250] if error else _("Seit über 2 Tagen kein neues Backup."),
                              url="/settings#backup", tag="backup")
    state["alerted"] = True
    _save_state(state)
    log.warning("Backup-Alarm gesendet (%s)", error or "veraltet")
    return True


def check_once(now: datetime | None = None) -> None:
    now = now or datetime.now(ZoneInfo(settings.timezone))
    if due(now) and not _recent_failure(now):
        try:
            run(now)
        except Exception:
            log.exception("Backup fehlgeschlagen")
    check_alarm(now)


def _recent_failure(now: datetime) -> bool:
    """Nach einem Fehlschlag frühestens nach einer Stunde erneut versuchen."""
    err = _state().get("error", "")
    try:
        at = datetime.strptime(err[:16], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
    except ValueError:
        return False
    return now - at < timedelta(hours=1)


async def run_forever() -> None:
    await asyncio.sleep(60)   # Start abwarten
    while True:
        try:
            await asyncio.to_thread(check_once)
        except Exception:
            log.exception("Backup-Prüfung fehlgeschlagen")
        await asyncio.sleep(CHECK_SECONDS)
