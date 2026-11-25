"""Zeitkonto (K7): Datumslogik (Deckel, Verfall, Kontogrenze, Jahreswechsel, Ferien), Policy, Nachmeldung, WebUI."""

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from kidctl_common import protocol as p

from app import policy, schedule as sch, timebank as tb
from app.models import Child, DayException, UsageRecord, Vacation, VacationRule, WeekdayRule
from conftest import SessionLocal, csrf_from, login, recv
from test_policy import _policy, device

WED = date(2026, 11, 25)


@pytest.fixture
def today(monkeypatch):
    def set_day(d: date):
        monkeypatch.setattr(sch, "today_local", lambda: d)
    return set_day


def _child(client, parent, **bank) -> tuple[int, str]:
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    with SessionLocal() as db:
        child = db.scalar(select(Child))
        for r in db.scalars(select(WeekdayRule).where(WeekdayRule.child_id == child.id)):
            r.quota_minutes, r.window_start, r.window_end = 60, time(8), time(20)
        for k, v in bank.items():
            setattr(child, f"bank_{k}", v)
        db.commit()
        return child.id, tok


def _usage(cid, minutes_by_day: dict[date, int]):
    with SessionLocal() as db:
        for d, m in minutes_by_day.items():
            db.add(UsageRecord(device_id=None, username="max", day=d, child_id=cid, seconds=m * 60 + 59))
        db.commit()


def _sim(cid, first, last):
    with SessionLocal() as db:
        return tb.simulate(db, db.get(Child, cid), first, last)


def _day(cid, d):
    with SessionLocal() as db:
        return tb.for_day(db, db.get(Child, cid), d)


def test_disabled_or_not_started_has_no_bank(client, parent, today):
    today(WED)
    cid, _ = _child(client, parent)
    _usage(cid, {WED - timedelta(days=1): 0})
    assert _sim(cid, WED - timedelta(days=5), WED) == {}
    with SessionLocal() as db:
        db.get(Child, cid).bank_enabled = True     # ohne Starttag: nichts
        db.commit()
    assert _day(cid, WED) == tb.BankDay(0, 0)


def test_deposit_spend_cap_fifo(client, parent, today):
    today(WED)
    cid, _ = _child(client, parent, enabled=True, since=date(2026, 11, 20), percent=50, cap_minutes=30,
                    expiry_days=7)
    _usage(cid, {date(2026, 11, 20): 20, date(2026, 11, 21): 60, date(2026, 11, 22): 75,
                 date(2026, 11, 23): 0, date(2026, 11, 24): 60})
    sim = _sim(cid, date(2026, 11, 20), WED + timedelta(days=3))
    assert sim == {
        date(2026, 11, 20): tb.BankDay(0, 0),
        date(2026, 11, 21): tb.BankDay(20, 20),    # 50 % von 40 nicht genutzten Minuten
        date(2026, 11, 22): tb.BankDay(20, 20),    # 21.: alles genutzt, nichts verbraucht
        date(2026, 11, 23): tb.BankDay(5, 5),      # 22.: 15 min über dem Kontingent aus dem Konto
        date(2026, 11, 24): tb.BankDay(30, 35),    # 23.: +30, Deckel je Tag 30
        WED: tb.BankDay(30, 35),                   # Zukunft: keine Einträge
    }
    with SessionLocal() as db:
        st = policy.day_status(db, db.get(Child, cid))
    assert (st.allowed_minutes, st.bank_minutes, st.bank_balance) == (90, 30, 35)


def test_expiry_and_balance_limit(client, parent, today):
    today(WED)
    cid, _ = _child(client, parent, enabled=True, since=date(2026, 11, 20), percent=100, cap_minutes=10,
                    expiry_days=2)
    _usage(cid, {date(2026, 11, 20): 50, date(2026, 11, 21): 60, date(2026, 11, 22): 60, date(2026, 11, 23): 60,
                 date(2026, 11, 24): 30})
    sim = _sim(cid, date(2026, 11, 21), WED)
    assert sim[date(2026, 11, 22)] == tb.BankDay(10, 10)     # Einzahlung vom 20. gilt am 21. und 22.
    assert sim[date(2026, 11, 23)] == tb.BankDay(0, 0)       # verfallen
    assert sim[WED] == tb.BankDay(10, 30)                    # 30 Restminuten, Konto höchstens 3 × 10


def test_credit_neither_deposits_nor_spends(client, parent, today):
    today(WED)
    cid, _ = _child(client, parent, enabled=True, since=date(2026, 11, 22), percent=100, cap_minutes=30,
                    expiry_days=7)
    _usage(cid, {date(2026, 11, 22): 50, date(2026, 11, 23): 80, date(2026, 11, 24): 0})
    with SessionLocal() as db:
        db.add_all([DayException(child_id=cid, day=date(2026, 11, 23), extra_minutes=30),
                    DayException(child_id=cid, day=date(2026, 11, 24), extra_minutes=45)])
        db.commit()
    sim = _sim(cid, date(2026, 11, 23), WED)
    assert sim[date(2026, 11, 24)] == tb.BankDay(10, 10)     # 23.: 20 min über der Regel, von der Gutschrift gedeckt
    assert sim[WED] == tb.BankDay(30, 70)                     # 24.: nur die 60 Regel-Minuten, nicht die Gutschrift


def test_year_change_and_vacation_rule(client, parent, today):
    today(date(2027, 1, 2))
    cid, _ = _child(client, parent, enabled=True, since=date(2026, 12, 30), percent=50, cap_minutes=120,
                    expiry_days=7)
    with SessionLocal() as db:
        db.add(Vacation(name="Weihnachten", first_day=date(2026, 12, 21), last_day=date(2027, 1, 1)))
        db.add_all(VacationRule(child_id=cid, weekday=d, quota_minutes=180, window_start=time(8),
                                window_end=time(20)) for d in range(7))
        db.commit()
    _usage(cid, {date(2026, 12, 30): 60, date(2026, 12, 31): 180, date(2027, 1, 1): 100})
    sim = _sim(cid, date(2026, 12, 30), date(2027, 1, 2))
    assert sim[date(2026, 12, 31)] == tb.BankDay(60, 60)      # Ferienvorlage: 180 − 60 = 120, davon 50 %
    assert sim[date(2027, 1, 1)] == tb.BankDay(60, 60)
    assert sim[date(2027, 1, 2)] == tb.BankDay(100, 100)       # +40 aus dem 1.1.


def test_settings_form(client, parent, today):
    today(WED)
    cid, tok = _child(client, parent)
    page = client.get(f"/children/{cid}/zeiten").text
    assert 'id="zeitkonto"' in page and 'name="bank_enabled"' in page
    r = client.post(f"/children/{cid}/time-bank", data={"csrf_token": tok, "bank_enabled": "1", "bank_percent": "25",
                                                        "bank_cap_minutes": "45", "bank_expiry_days": "3"},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        c = db.get(Child, cid)
        assert (c.bank_enabled, c.bank_since, c.bank_percent, c.bank_cap_minutes, c.bank_expiry_days) == \
            (True, WED + timedelta(days=1), 25, 45, 3)
    assert "gutgeschrieben wird ab" in client.get(f"/children/{cid}/zeiten").text
    # erneutes Speichern verschiebt den Starttag nicht
    today(WED + timedelta(days=3))
    client.post(f"/children/{cid}/time-bank", data={"csrf_token": tok, "bank_enabled": "1", "bank_percent": "50",
                                                    "bank_cap_minutes": "45", "bank_expiry_days": "3"})
    with SessionLocal() as db:
        assert db.get(Child, cid).bank_since == WED + timedelta(days=1)
    assert "Kontostand:" in client.get(f"/children/{cid}/zeiten").text
    assert "Zeitkonto" in client.get(f"/children/{cid}").text
    for bad in ({"bank_percent": "30"}, {"bank_cap_minutes": "500"}, {"bank_expiry_days": "0"},
                {"bank_cap_minutes": "x"}):
        data = {"csrf_token": tok, "bank_enabled": "1", "bank_percent": "50", "bank_cap_minutes": "30",
                "bank_expiry_days": "7", **bad}
        assert client.post(f"/children/{cid}/time-bank", data=data).status_code == 400
    client.post(f"/children/{cid}/time-bank", data={"csrf_token": tok, "bank_percent": "50",
                                                    "bank_cap_minutes": "30", "bank_expiry_days": "7"})
    with SessionLocal() as db:
        c = db.get(Child, cid)
        assert not c.bank_enabled and c.bank_since is None
    assert "Zeitkonto" not in client.get(f"/children/{cid}").text.split("today-facts")[1].split("</dl>")[0]


def test_policy_contains_bank_and_late_report_updates_it(client, parent, today):
    today(WED)
    cid, tok = _child(client, parent, enabled=True, since=WED - timedelta(days=3), percent=50, cap_minutes=30,
                      expiry_days=7)
    _usage(cid, {WED - timedelta(days=3): 60, WED - timedelta(days=2): 60, WED - timedelta(days=1): 0})
    with device(client, tok, "pc", map_to=cid, proto=2) as (ws, _dev):
        pol = _policy(ws)
        bank = [e for e in pol.children[0].exceptions if e.day == WED]
        assert [e.extra_minutes for e in bank] == [30]
        ws.send_text(p.PolicyAck(version=pol.version).model_dump_json())
        # Nachmeldung: gestern doch 50 min genutzt -> nur noch 5 min Guthaben, Policy kommt neu
        ws.send_text(p.UsageReport(entries=[p.UsageEntry(username="max", day=WED - timedelta(days=1),
                                                         seconds=50 * 60)]).model_dump_json())
        new = _policy(ws)
        assert [e.extra_minutes for e in new.children[0].exceptions if e.day == WED] == [5]
        assert new.version != pol.version


def test_policy_without_bank_unchanged(client, parent, today):
    today(WED)
    cid, tok = _child(client, parent)
    _usage(cid, {WED - timedelta(days=1): 0})
    with device(client, tok, "pc", map_to=cid, proto=2) as (ws, _dev):
        assert _policy(ws).children[0].exceptions == []
