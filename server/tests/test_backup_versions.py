"""Datenbank-Backup (Schnappschuss, Aufbewahrung, Fälligkeit, Alarm) und Hinweis auf veraltete Agenten."""

import gzip
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import alarms, backup, versions
from app.config import Settings
from app.db import SessionLocal
from app.models import Device, Parent, utcnow
from conftest import csrf_from, login

TZ = ZoneInfo("Europe/Berlin")


def _clear():
    for f in backup.backup_dir().iterdir():
        f.unlink()


def test_snapshot_is_valid_and_pruned(client, parent):
    _clear()
    base = datetime(2026, 10, 1, 3, 5, tzinfo=TZ)
    for i in range(backup.KEEP + 3):
        info = backup.run(base + timedelta(days=i))
        os.utime(info.path, (info.at.timestamp(), (base + timedelta(days=i)).timestamp()))
    found = backup.backups()
    assert len(found) == backup.KEEP and found[0].path.name == "kidctl-2026-10-17-0305.db.gz"
    raw = backup.backup_dir() / "check.db"
    raw.write_bytes(gzip.decompress(found[0].path.read_bytes()))
    with sqlite3.connect(raw) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT username FROM parent").fetchall() == [("mama",)]
    raw.unlink()
    assert oct(found[0].path.stat().st_mode & 0o777) == "0o600"


def test_due_and_alarm(client, parent, monkeypatch):
    _clear()
    sent = []
    monkeypatch.setattr(backup.push, "send_to_parents_sync",
                        lambda title, body, url="/", tag=None, parent_id=None: sent.append((title(), body())))
    now = datetime.now(TZ).replace(hour=2, minute=0)
    assert not backup.due(now) and backup.due(now.replace(hour=3))
    backup.check_alarm(now.replace(hour=3, minute=30))
    assert sent == []                                              # erster Versuch steht noch aus
    monkeypatch.setattr(backup, "engine", type("E", (), {"url": type("U", (), {"database": "/nicht/da/x.db"})()})())
    backup.check_once(now.replace(hour=4))
    assert backup.last_error()                                     # Fehler vermerkt
    assert sent and sent[0][0] == "Datenbank-Backup fehlgeschlagen"
    backup.check_once(now.replace(hour=4, minute=10))              # gleiche Störung: kein zweiter Alarm
    assert len(sent) == 1
    monkeypatch.undo()
    info = backup.run(now.replace(hour=5))
    assert backup.last_error() == "" and not backup.due(now.replace(hour=6))
    os.utime(info.path, ((now - timedelta(days=3)).timestamp(),) * 2)
    monkeypatch.setattr(backup.push, "send_to_parents_sync",
                        lambda title, body, url="/", tag=None, parent_id=None: sent.append((title(), body())))
    assert backup.check_alarm(now) is True and sent[-1][0] == "Datenbank-Backup veraltet"
    assert backup.check_alarm(now) is False


def test_settings_page_and_manual_backup(client, parent):
    _clear()
    login(client, parent)
    page = client.get("/settings").text
    assert "Datenbank-Backup" in page and "Noch kein Backup vorhanden." in page and "Einstellungen" in page
    r = client.post("/settings/backup", data={"csrf_token": csrf_from(page)}, follow_redirects=True)
    assert "Backup erstellt." in r.text and "Letztes Backup:" in r.text and "1 Backup vorhanden" in r.text


def _repo(tmp_path, *names, age_days=2):
    repo = tmp_path / "repo"
    repo.mkdir()
    for n in names:
        f = repo / n
        f.write_text("")
        t = (datetime.now() - timedelta(days=age_days)).timestamp()
        os.utime(f, (t, t))
    os.utime(repo, (1, 1))
    return repo


def test_latest_version_from_repo(tmp_path):
    repo = _repo(tmp_path, "kidctl-0.9.1-1-any.pkg.tar.zst", "kidctl-0.26.2-1-any.pkg.tar.zst",
                 "kidctl-0.26.10-1-any.pkg.tar.zst", "kidctl.db.tar.zst", "kidctl-0.26.2-1-any.pkg.tar.zst.sig")
    rel = versions.latest(repo)
    assert rel.version == "0.26.10"
    assert versions.update_for("0.26.2", rel) == "0.26.10" and versions.update_for("0.26.10", rel) is None
    assert versions.update_for("", rel) is None and versions.update_for("t", rel) is None
    assert versions.latest(tmp_path / "fehlt") is None


def test_update_hint_and_alarm_once(client, parent, tmp_path, monkeypatch):
    repo = _repo(tmp_path, "kidctl-0.26.2-1-any.pkg.tar.zst")
    monkeypatch.setattr(versions, "_cache", None)
    rel = versions.latest(repo)
    monkeypatch.setattr(versions, "latest", lambda repo=None: rel)
    with SessionLocal() as db:
        db.add_all([Device(name="PC", token_hash="a" * 64, agent_version="0.26.1"),
                    Device(name="Neu", token_hash="b" * 64, agent_version="0.26.2")])
        db.commit()
        due = alarms.due_update_alarms(db)
        assert [(d.name, v) for d, v in due] == [("PC", "0.26.2")]
        db.commit()
        assert alarms.due_update_alarms(db) == []                            # nur einmal je Version
        fresh = versions.Release("0.26.3", utcnow() - timedelta(hours=2))
        assert alarms.due_update_alarms(db, fresh) == []                     # erst nach einem Tag
    login(client, parent)
    assert "Update: 0.26.1 → 0.26.2" in client.get("/devices").text
    assert "Update: 0.26.1 → 0.26.2" in client.get("/").text
    with SessionLocal() as db:
        dev_id = db.scalar(select(Device.id).where(Device.name == "PC"))
    assert "Update verfügbar: 0.26.2" in client.get(f"/devices/{dev_id}").text


def test_download_requires_password(client, parent):
    _clear()
    login(client, parent)
    info = backup.run()
    page = client.get("/settings").text
    tok = csrf_from(page)
    assert "Herunterladen" in page
    r = client.post("/settings/backup/download", data={"csrf_token": tok, "name": info.path.name, "current_password": "falsch"})
    assert r.status_code == 400 and "Aktuelles Passwort ist falsch." in r.text
    r = client.post("/settings/backup/download", data={"csrf_token": tok, "name": "../kidctl.db", "current_password": "x"})
    assert r.status_code == 400 and "Backup nicht gefunden." in r.text
    from conftest import PASSWORD
    r = client.post("/settings/backup/download", data={"csrf_token": tok, "name": info.path.name, "current_password": PASSWORD})
    assert r.status_code == 200 and gzip.decompress(r.content)[:15] == b"SQLite format 3"


def test_latest_version_from_apt_pool(tmp_path):
    repo = _repo(tmp_path, "kidctl-0.27.0-1-any.pkg.tar.zst", "kidctl-plasma-0.29.0-1-any.pkg.tar.zst")
    pool = repo / "deb" / "pool" / "main"
    pool.mkdir(parents=True)
    for n in ("kidctl_0.28.0-1_amd64.deb", "kidctl-gnome_0.30.0-1_all.deb"):
        (pool / n).write_text("")
    assert versions.latest(repo).version == "0.28.0"   # Desktop-Pakete zählen nicht
