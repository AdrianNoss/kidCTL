"""Nutzungsbericht per E-Mail: Zeiträume, Fälligkeit, Inhalt, Versand, Profil-Einstellungen."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import mail, report
from app.config import Settings
from app.db import SessionLocal
from app.models import (
    AppUsage, BlockedEvent, Child, DayException, Device, GameSession, Message, Parent, UsageRecord, WeekdayRule,
)
from conftest import csrf_from, login

TZ = ZoneInfo("Europe/Berlin")
WEEK = report.Period("weekly", date(2026, 10, 12), date(2026, 10, 18))   # Mo–So


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None: sent.append((to, subject, body, html)) or True)
    report._failed.clear()
    return sent


def _set(parent, **kw):
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        for k, v in kw.items():
            setattr(p, k, v)
        db.commit()


def _utc(d: date, h: int) -> datetime:
    return datetime.combine(d, time(h), TZ).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _seed() -> int:
    with SessionLocal() as db:
        dev = Device(name="PC", token_hash="x" * 64)
        child = Child(name="Lea & Tom")
        child.rules = [WeekdayRule(weekday=d, quota_minutes=60) for d in range(7)]
        db.add_all([dev, child])
        db.flush()
        for offset, minutes in [(0, 30), (1, 90), (5, 45), (-7, 60)]:      # -7: Vorwoche
            db.add(UsageRecord(device_id=dev.id, username="lea", child_id=child.id,
                               day=WEEK.start + timedelta(days=offset), seconds=minutes * 60))
        db.add(DayException(child_id=child.id, day=WEEK.start + timedelta(days=5), extra_minutes=15))
        db.add_all([
            AppUsage(device_id=dev.id, username="lea", child_id=child.id, day=WEEK.start, key="exe:/usr/bin/krita",
                     seconds=40 * 60),
            AppUsage(device_id=dev.id, username="lea", child_id=child.id, day=WEEK.start, key="game:steam:620",
                     name="Portal 2", seconds=50 * 60),
            AppUsage(device_id=dev.id, username="lea", child_id=child.id, day=WEEK.end + timedelta(days=1),
                     key="exe:/usr/bin/later", seconds=600),                                  # nach dem Zeitraum
            GameSession(child_id=child.id, device_id=dev.id, username="lea", key="game:steam:620", name="Portal 2",
                        started_at=_utc(WEEK.start, 15), last_seen_at=_utc(WEEK.start, 16)),
            BlockedEvent(child_id=child.id, device_id=dev.id, username="lea", at=_utc(WEEK.start, 10), kind="domain",
                         key="domain:tiktok.com", name="tiktok.com"),
            BlockedEvent(child_id=child.id, device_id=dev.id, username="lea", at=_utc(WEEK.start, 11), kind="domain",
                         key="domain:tiktok.com", name="tiktok.com"),
            BlockedEvent(child_id=child.id, device_id=dev.id, username="lea", at=_utc(WEEK.start, 11), kind="program",
                         key="exe:/usr/bin/x", name="x", audit=True),                            # nur protokolliert
            BlockedEvent(child_id=child.id, device_id=dev.id, username="lea", at=_utc(WEEK.end + timedelta(days=1), 1),
                         kind="domain", key="domain:late.com", name="late.com"),                 # nach dem Zeitraum
            Message(child_id=child.id, from_child=True, kind="extension_request", created_at=_utc(WEEK.start, 12),
                    status="approved"),
            Message(child_id=child.id, from_child=True, kind="extension_request", created_at=_utc(WEEK.start, 13),
                    status="rejected"),
        ])
        db.commit()
        return child.id


def test_periods():
    assert report.last_period("weekly", date(2026, 10, 19)) == WEEK            # Montag
    assert report.last_period("weekly", date(2026, 10, 25)) == WEEK            # Sonntag
    m = report.last_period("monthly", date(2026, 3, 1))
    assert (m.start, m.end, m.key, m.label) == (date(2026, 2, 1), date(2026, 2, 28), "2026-02", "Februar 2026")
    assert WEEK.key == "2026-W42" and WEEK.previous().end == date(2026, 10, 11)
    assert m.previous() == report.Period("monthly", date(2026, 1, 1), date(2026, 1, 31))
    jan = report.last_period("monthly", date(2027, 1, 5))
    assert (jan.start, jan.end) == (date(2026, 12, 1), date(2026, 12, 31))


def test_due():
    p = Parent(username="a", email="a@example.org", report_interval="weekly", report_sent="")
    monday = datetime(2026, 10, 19, 6, 30, tzinfo=TZ)
    assert report.due(p, monday) is None                                        # vor der Versandzeit
    assert report.due(p, monday.replace(hour=7)) == WEEK
    assert report.due(p, monday + timedelta(days=2, hours=-6)) == WEEK         # nachgeholt (Mittwoch früh)
    p.report_sent = WEEK.key
    assert report.due(p, monday.replace(hour=9)) is None
    p.email = ""
    p.report_sent = ""
    assert report.due(p, monday.replace(hour=9)) is None                        # ohne Adresse nichts
    p.email, p.report_interval = "a@example.org", ""
    assert report.due(p, monday.replace(hour=9)) is None
    report.activate(p, "monthly", date(2026, 10, 20))
    assert p.report_sent == "2026-09" and report.due(p, datetime(2026, 10, 25, 9, tzinfo=TZ)) is None
    assert report.due(p, datetime(2026, 11, 1, 7, 5, tzinfo=TZ)).key == "2026-10"


def test_content(client, parent):
    cid = _seed()
    with SessionLocal() as db:
        r = report.child_report(db, db.get(Child, cid), WEEK)
        assert [d.used for d in r.days] == [30, 90, 0, 0, 0, 45, 0]
        assert r.total == 165 and r.prev_total == 60 and r.change == 175 and r.avg == 24 and r.active_days == 3
        assert [d.day for d in r.over_days] == [date(2026, 10, 13)]             # Sa: 45 von 60+15 nicht
        assert [a.name for a in r.apps] == ["krita"] and [(g.name, g.launcher) for g in r.games] == [("Portal 2", "Steam")]
        assert r.game_sessions == 1 and r.blocked == 2 and r.blocked_top == [("domain", "tiktok.com", 2)]
        assert (r.requests, r.approved, r.credits) == (2, 1, 15)

        subject, text, html = report.render(db, db.get(Parent, parent.id), WEEK)
    assert subject == "KidCTL: Wochenbericht 12.10.–18.10.2026"
    assert "Lea &amp; Tom" in html and "Lea & Tom" in text and "&amp;" not in text
    assert "Portal 2" in html and "tiktok.com" in html and "/children/%d/stats" % cid in html
    assert "Di 13.10. (+30 min)" in html and "Di 13.10. (+30 min)" in text
    assert "+175 % ggü. Vorwoche" in html


def test_send_due_once(client, parent, outbox):
    _seed()
    _set(parent, email="mama@example.org", report_interval="weekly", report_sent="")
    now = datetime(2026, 10, 19, 7, 10, tzinfo=TZ)
    with SessionLocal() as db:
        assert report.send_due(db, now) == 1
        assert report.send_due(db, now + timedelta(hours=3)) == 0
        assert db.get(Parent, parent.id).report_sent == "2026-W42"
    to, subject, body, html = outbox[0]
    assert to == "mama@example.org" and "Wochenbericht" in subject and html.startswith("<!doctype html>")


def test_send_failure_retries_later(client, parent, monkeypatch):
    calls = []
    monkeypatch.setattr(report, "send", lambda db, p, period: calls.append(period) or False)
    report._failed.clear()
    _set(parent, email="mama@example.org", report_interval="weekly", report_sent="")
    now = datetime(2026, 10, 19, 8, tzinfo=TZ)
    with SessionLocal() as db:
        assert report.send_due(db, now) == 0 and report.send_due(db, now) == 0
        assert len(calls) == 1 and db.get(Parent, parent.id).report_sent == ""   # Wiederholung erst nach 1 h
        report._failed[parent.id] -= report.RETRY_SECONDS
        report.send_due(db, now)
        assert len(calls) == 2


def test_profile_settings(client, parent, outbox):
    login(client, parent)
    page = client.get("/profile")
    assert "Nutzungsbericht" in page.text
    token = csrf_from(page.text)
    r = client.post("/profile/report", data={"csrf_token": token, "interval": "weekly"}, follow_redirects=False)
    assert r.status_code == 400 and "E-Mail-Adresse" in r.text                   # erst Adresse eintragen
    _set(parent, email="mama@example.org")
    assert client.post("/profile/report", data={"csrf_token": token, "interval": "quarterly"}).status_code == 400
    r = client.post("/profile/report", data={"csrf_token": token, "interval": "monthly"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        assert p.report_interval == "monthly" and p.report_sent      # letzter Monat gilt als gesendet
    assert client.post("/profile/report", data={"interval": "weekly"}).status_code == 403   # CSRF

    _seed()
    prev = client.get("/profile/report/preview?interval=weekly")
    assert prev.status_code == 200 and "Wochenbericht" in prev.text and "Lea &amp; Tom" in prev.text
    assert "Monatsbericht" in client.get("/profile/report/preview").text

    r = client.post("/profile/report/send", data={"csrf_token": token, "interval": "weekly"}, follow_redirects=False)
    assert r.status_code == 303 and len(outbox) == 1 and "Wochenbericht" in outbox[0][1]
    client.post("/profile/report", data={"csrf_token": token, "interval": ""})
    with SessionLocal() as db:
        assert db.get(Parent, parent.id).report_interval == ""
