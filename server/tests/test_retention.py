"""Aufbewahrungsfristen und „Chat leeren“ (Etappe 2.5 Multi-Tenant)."""

from datetime import timedelta

from sqlalchemy import func, select

from app import retention
from app.models import (
    AppUsage, BlockedEvent, Child, Device, LoginAttempt, Message, PairingCode, ParentSession, UsageRecord, utcnow,
)
from conftest import SessionLocal, csrf_from, login, platform_db


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_prune_keeps_recent_and_removes_old_per_rule(client, parent, monkeypatch):
    now = utcnow()
    with SessionLocal() as db:
        child, dev = Child(name="Lena"), Device(name="PC", token_hash="p" * 64)
        db.add_all([child, dev])
        db.flush()
        old, new = now - timedelta(days=400), now - timedelta(days=10)
        db.add_all([
            BlockedEvent(child_id=child.id, username="lena", at=old, key="exe:/x", name="x"),
            BlockedEvent(child_id=child.id, username="lena", at=new, key="exe:/x", name="x"),
            AppUsage(device_id=dev.id, username="lena", day=(now - timedelta(days=100)).date(), key="k", seconds=60),
            UsageRecord(device_id=dev.id, username="lena", day=(now - timedelta(days=380)).date(), seconds=60),  # < 13 Monate
            UsageRecord(device_id=dev.id, username="lena", day=(now - timedelta(days=400)).date(), seconds=60),
            Message(child_id=child.id, from_child=True, body="alt", created_at=now - timedelta(days=370)),
            Message(child_id=child.id, from_child=True, body="neu", created_at=new),
            LoginAttempt(ip="1.2.3.4", at=now - timedelta(days=31)),
            ParentSession(parent_id=parent.id, token_hash="t" * 64, csrf_token="c", expires_at=now - timedelta(days=31)),
            PairingCode(code_hash="k" * 64, device_name="Lenas PC", expires_at=now - timedelta(days=2)),
        ])
        db.commit()
    with platform_db() as db:
        removed = retention.prune_all(db)
        assert removed == {"LOG": 1, "APPS": 1, "USAGE": 1, "CHAT": 1, "LOGINS": 1, "SESSIONS": 1, "PAIRING": 1}
        assert _count(db, BlockedEvent) == 1 and _count(db, UsageRecord) == 1 and _count(db, Message) == 1
    monkeypatch.setenv("KIDCTL_KEEP_LOG_DAYS", "5")                  # per Umgebung kürzer
    with platform_db() as db:
        assert retention.prune_all(db) == {"LOG": 1}


def test_clear_chat_only_this_child(client, parent):
    with SessionLocal() as db:
        a, b = Child(name="A"), Child(name="B")
        db.add_all([a, b])
        db.flush()
        db.add_all([Message(child_id=a.id, from_child=True, body="weg"), Message(child_id=b.id, from_child=True, body="bleibt")])
        db.commit()
        ids = a.id, b.id
    login(client, parent)
    page = client.get(f"/children/{ids[0]}/chat").text
    assert "Chat leeren" in page
    r = client.post(f"/children/{ids[0]}/chat/clear", data={"csrf_token": csrf_from(page)}, follow_redirects=True)
    assert "Chat geleert." in r.text and "weg" not in r.text
    with SessionLocal() as db:
        assert [m.body for m in db.scalars(select(Message))] == ["bleibt"]
