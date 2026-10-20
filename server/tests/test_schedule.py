"""Smoke-Tests Bildschirmzeit: Wochenvorlage, Gruppenmodus, Gutschriften (Tagesausnahmen)."""

from datetime import time, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Child, DayException
from app.schedule import today_local

from conftest import csrf_from, login


def _setup(client, parent) -> tuple[int, str]:
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    with SessionLocal() as db:
        return db.scalar(select(Child.id)), tok


def _rules(child_id):
    with SessionLocal() as db:
        return {r.weekday: (r.quota_minutes, r.window_start, r.window_end)
                for r in db.get(Child, child_id).rules}


def test_group_mode_writes_all_days(client, parent):
    cid, tok = _setup(client, parent)
    page = client.get(f"/children/{cid}")
    assert page.status_code == 200 and "Werktage (Mo–Fr)" in page.text
    r = client.post(f"/children/{cid}/schedule", data={
        "csrf_token": tok, "quota_wd": "90", "start_wd": "14:00", "end_wd": "19:30",
        "quota_we": "180", "start_we": "09:00", "end_we": "21:00"}, follow_redirects=False)
    assert r.status_code == 303
    rules = _rules(cid)
    assert all(rules[d] == (90, time(14), time(19, 30)) for d in range(5))
    assert all(rules[d] == (180, time(9), time(21)) for d in (5, 6))


def test_individual_mode_and_differs_hint(client, parent):
    cid, tok = _setup(client, parent)
    client.post(f"/children/{cid}/mode", data={"csrf_token": tok, "weekdays_same": ""})
    page = client.get(f"/children/{cid}")
    assert "Mittwoch" in page.text
    data = {"csrf_token": tok}
    for d in range(7):
        data.update({f"quota_{d}": str(30 + d * 10), f"start_{d}": "08:00", f"end_{d}": "20:00"})
    assert client.post(f"/children/{cid}/schedule", data=data, follow_redirects=False).status_code == 303
    assert _rules(cid)[3][0] == 60
    # Zurück in den Gruppenmodus: Regeln bleiben unverändert, Hinweis erscheint
    client.post(f"/children/{cid}/mode", data={"csrf_token": tok, "weekdays_same": "1"})
    page = client.get(f"/children/{cid}")
    assert "Unterschiedliche Tageswerte" in page.text
    assert _rules(cid)[3][0] == 60


def test_schedule_validation(client, parent):
    cid, tok = _setup(client, parent)
    before = _rules(cid)
    bad = {"csrf_token": tok, "quota_wd": "90", "start_wd": "20:00", "end_wd": "08:00",
           "quota_we": "abc", "start_we": "09:00", "end_we": "21:00"}
    r = client.post(f"/children/{cid}/schedule", data=bad)
    assert r.status_code == 400 and "Fensterbeginn" in r.text
    bad.update(start_wd="08:00", end_wd="20:00")
    assert client.post(f"/children/{cid}/schedule", data=bad).status_code == 400
    assert _rules(cid) == before  # nichts teilweise gespeichert


def test_credit_creates_and_accumulates_exception(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local().isoformat()
    for _ in range(2):
        r = client.post(f"/children/{cid}/credit", data={
            "csrf_token": tok, "day": today, "extra_minutes": "30", "window_end": ""},
            follow_redirects=False)
        assert r.status_code == 303
    r = client.post(f"/children/{cid}/credit", data={
        "csrf_token": tok, "day": today, "extra_minutes": "0", "window_end": "21:30"},
        follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        exc = db.scalar(select(DayException).where(DayException.child_id == cid))
        assert exc.extra_minutes == 60 and exc.window_end_override == time(21, 30)
        exc_id = exc.id
    page = client.get(f"/children/{cid}").text
    assert "(heute)" in page and "1:00 h" in page and "21:30" in page

    client.post(f"/children/{cid}/exceptions/{exc_id}/delete", data={"csrf_token": tok})
    with SessionLocal() as db:
        assert db.get(DayException, exc_id) is None


def test_credit_validation(client, parent):
    cid, tok = _setup(client, parent)
    today = today_local()
    cases = [
        ({"day": (today - timedelta(days=1)).isoformat(), "extra_minutes": "30"}, "heute oder künftige"),
        ({"day": today.isoformat(), "extra_minutes": "0"}, "Spielzeit und/oder"),
        ({"day": today.isoformat(), "extra_minutes": "0", "window_end": "19:00"}, "nach dem regulären Ende"),
        ({"day": today.isoformat(), "extra_minutes": "9999"}, "zwischen 0 und"),
        ({"day": "kaputt", "extra_minutes": "30"}, "Ungültiges Datum"),
    ]
    for data, msg in cases:
        r = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, **data})
        assert r.status_code == 400 and msg in r.text, (data, r.text[:200])
    with SessionLocal() as db:
        assert db.scalar(select(DayException)) is None


def test_credit_requires_csrf(client, parent):
    cid, _ = _setup(client, parent)
    r = client.post(f"/children/{cid}/credit", data={"day": today_local().isoformat(), "extra_minutes": "30"})
    assert r.status_code == 403
