"""Datenbank-Backup: täglich ab ``BACKUP_HOUR`` Uhr ein konsistenter PostgreSQL-Dump (``pg_dump``, komprimiertes
Custom-Format, auch während der Server schreibt), geprüft (``pg_restore --list``), nach ``<data>/backups``. Die
neuesten ``KEEP`` bleiben. Wiederherstellen: ``pg_restore --clean --if-exists -d <db> <datei>``.
Programme per ``KIDCTL_PG_DUMP`` / ``KIDCTL_PG_RESTORE`` änderbar (Tests: Aufruf im Datenbank-Container). Alarm per Push, wenn das letzte Backup älter als ``ALERT_AFTER`` ist oder ein
Versuch fehlschlägt – einmal je Störung, erneut erst nach einem erfolgreichen Backup.

Das externe Backup des Hosts sichert ``./data`` und damit auch diese Schnappschüsse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import push
from .config import settings
from .i18n import _

log = logging.getLogger("kidctl.backup")

BACKUP_HOUR = 3
KEEP = 14
ALERT_AFTER = timedelta(days=2)
CHECK_SECONDS = 600
PREFIX, SUFFIX = "kidctl-", ".dump"
DUMP_TIMEOUT = 900
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
            dump = Path(tmp) / "snapshot.dump"
            with dump.open("wb") as out:
                _run(_tool("KIDCTL_PG_DUMP", "pg_dump") + ["--format=custom", "--no-owner", "--no-privileges"], stdout=out)
            with dump.open("rb") as inp:
                listing = _run(_tool("KIDCTL_PG_RESTORE", "pg_restore") + ["--list"], stdin=inp).stdout
            if b"TABLE DATA public parent" not in listing:
                raise RuntimeError("Dump unvollständig (Tabelle parent fehlt)")
            os.chmod(dump, 0o600)
            os.replace(dump, target)
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


def _tool(env: str, default: str) -> list[str]:
    return shlex.split(os.environ.get(env, default))


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """pg_dump/pg_restore mit den Verbindungsdaten als Umgebung (Passwort nie in der Befehlszeile)."""
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    env = {**os.environ, "PGHOST": url.host or "", "PGPORT": str(url.port or 5432), "PGUSER": url.username or "",
           "PGPASSWORD": url.password or "", "PGDATABASE": url.database or ""}
    kw.setdefault("stdout", subprocess.PIPE)
    res = subprocess.run(cmd, env=env, stderr=subprocess.PIPE, timeout=DUMP_TIMEOUT, **kw)
    if res.returncode:
        raise RuntimeError(f"{cmd[0]} fehlgeschlagen: {res.stderr.decode(errors='replace').strip()[:300]}")
    return res


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
