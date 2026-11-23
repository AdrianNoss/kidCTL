"""Ferien und Feiertage: Datumslogik, rollierende Wochenregeln in der Policy, Tageswechsel, WebUI."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from kidctl_common import protocol as p

from app import policy, schedule as sch, vacations as vac
from app.models import Child, HolidayRegion, Vacation, VacationRule
from app.tenant import family_session
from conftest import SessionLocal, add_family, csrf_from, login
from test_policy import _policy, device

MONDAY = date(2026, 4, 6)   # Ostermontag 2026


def _setup(client, parent) -> tuple[int, str]:
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    with SessionLocal() as db:
        return db.scalar(select(Child.id)), tok


@pytest.fixture
def today(monkeypatch):
    """Heimat-„Heute“ festlegen (Policy, Eingabeprüfung)."""
    def set_day(d: date):
        monkeypatch.setattr(sch, "today_local", lambda: d)
    return set_day


def _vacation_rules(client, cid, tok, quota=300):
    return client.post(f"/children/{cid}/vacation-schedule", data={
        "csrf_token": tok, "quota_wd": str(quota), "start_wd": "09:00", "end_wd": "21:00",
        "quota_we": str(quota + 60), "start_we": "09:00", "end_we": "22:00"}, follow_redirects=False)


# ---------- Feiertage (Bibliothek, lokal) ----------

def test_holiday_dates_by_region():
    by = vac.holiday_dates("DE", "BY", 2026)
    assert by[date(2026, 6, 4)] == "Fronleichnam" and date(2026, 1, 6) in by
    nw = vac.holiday_dates("DE", "NW", 2026)
    assert date(2026, 1, 6) not in nw and date(2026, 6, 4) in nw
    assert date(2026, 10, 31) in vac.holiday_dates("DE", "SN", 2026)   # Reformationstag
    de = vac.holiday_dates("DE", "", 2026)
    assert date(2026, 10, 3) in de and date(2026, 6, 4) not in de     # nur landesweit
    assert date(2026, 10, 26) in vac.holiday_dates("AT", "9", 2026)    # Nationalfeiertag
    assert date(2026, 8, 1) in vac.holiday_dates("CH", "ZH", 2026)
    assert vac.holiday_dates("DE", "", 2026, "en")[date(2026, 12, 25)] == "Christmas Day"


def test_subdivisions_have_names():
    subs = dict(vac.subdivisions("DE"))
    assert subs["BY"] == "Bayern" and subs["NW"] == "Nordrhein-Westfalen"
    assert dict(vac.subdivisions("AT"))["9"] == "Wien"
    assert vac.valid_region("CH", "ZH") and vac.valid_region("DE", "")
    assert not vac.valid_region("FR", "") and not vac.valid_region("DE", "XX")


# ---------- Geltende Regel eines Datums ----------

def _rule(wd, quota):
    return type("R", (), dict(weekday=wd, quota_minutes=quota, window_start=time(8), window_end=time(20),
                              game_minutes=None))()


class FakeChild:
    def __init__(self, vacation=True):
        self.rules = [_rule(d, 60 + d) for d in range(7)]            # Mo 60 … So 66
        self.vacation_rules = [_rule(d, 200 + d) for d in range(7)] if vacation else []


def _cal(vacations=(), region=None):
    return vac.Calendar([Vacation(name=n, first_day=a, last_day=b) for n, a, b in vacations], region)


def test_rule_for_weekday_vacation_holiday():
    child = FakeChild()
    region = HolidayRegion(country="DE", subdiv="BY", as_weekday=6)
    cal = _cal([("Ostern", date(2026, 3, 30), date(2026, 4, 10))], region)
    assert cal.rule_for(child, date(2026, 3, 27)).quota_minutes == 64            # Freitag, normal
    assert cal.rule_for(child, date(2026, 3, 30)).quota_minutes == 200           # erster Ferientag (Montag)
    assert cal.rule_for(child, date(2026, 4, 10)).quota_minutes == 204           # letzter Ferientag (einschl.)
    assert cal.rule_for(child, date(2026, 4, 11)).quota_minutes == 65            # danach wieder normal
    assert cal.rule_for(child, MONDAY).quota_minutes == 206                      # Feiertag in Ferien: Ferien-Sonntag
    assert cal.rule_for(child, date(2026, 6, 4)).quota_minutes == 66             # Fronleichnam: wie Sonntag
    region.as_weekday = 5
    assert cal.rule_for(child, date(2026, 6, 4)).quota_minutes == 65             # „gilt wie Samstag“
    assert cal.holiday(date(2026, 6, 4)) == "Fronleichnam"
    assert vac.day_label(cal, MONDAY) == "Ferien: Ostern · Feiertag: Ostermontag"


def test_vacation_without_template_uses_week_rules():
    child = FakeChild(vacation=False)
    cal = _cal([("", date(2026, 7, 1), date(2026, 8, 10))])
    assert cal.rule_for(child, date(2026, 7, 1)).quota_minutes == 62
    assert vac.day_label(cal, date(2026, 7, 1)) == "Ferien"


def test_week_rules_rolling_window():
    """Heute … heute+6, je Wochentag genau einmal, nach Wochentag sortiert."""
    child = FakeChild()
    cal = _cal([("Herbst", date(2026, 11, 2), date(2026, 11, 3))], HolidayRegion(country="DE", subdiv="", as_weekday=6))
    rules = cal.week_rules(child, date(2026, 10, 29))     # Do 29.10. … Mi 4.11.
    assert [wd for wd, _r in rules] == list(range(7))
    q = {wd: r.quota_minutes for wd, r in rules}
    assert q == {0: 200, 1: 201, 2: 62, 3: 63, 4: 64, 5: 65, 6: 66}
    # Ein Tag später fällt der Donnerstag (5.11.) wieder auf die normale Regel
    assert dict((wd, r.quota_minutes) for wd, r in cal.week_rules(child, date(2026, 10, 30)))[3] == 63
    # Tag der Deutschen Einheit (Sa 3.10.2026) gilt wie Sonntag
    assert dict((wd, r.quota_minutes) for wd, r in cal.week_rules(child, date(2026, 10, 1)))[5] == 66


def test_seconds_to_midnight_home_timezone():
    tz = ZoneInfo("Europe/Berlin")
    assert vac.seconds_to_midnight(datetime(2026, 11, 23, 23, 59, 30, tzinfo=tz)) == 30
    # Umstellung auf Sommerzeit: der 29.03.2026 hat nur 23 Stunden
    assert vac.seconds_to_midnight(datetime(2026, 3, 29, 0, 0, tzinfo=tz)) == 23 * 3600


def test_today_local_uses_home_timezone(monkeypatch):
    """23:30 UTC ist in Berlin schon der nächste Tag – Ferienbeginn gilt ab lokaler Mitternacht."""
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 31, 23, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz)
    monkeypatch.setattr(sch, "datetime", FakeDT)
    assert sch.today_local() == date(2026, 8, 1)


# ---------- Policy ----------

def test_policy_unchanged_without_vacations(client, parent, today):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        with SessionLocal() as db:
            base = policy.build_policy(db, dev_id).version
            today(sch.today_local() + timedelta(days=1))
            assert policy.build_policy(db, dev_id).version == base     # ohne Ferien/Feiertage tagesunabhängig


def test_policy_contains_vacation_and_holiday_rules(client, parent, today):
    today(date(2026, 4, 1))   # Mittwoch vor Ostern
    cid, tok = _setup(client, parent)
    assert _vacation_rules(client, cid, tok).status_code == 303
    r = client.post("/settings/vacations", data={"csrf_token": tok, "name": "Osterferien",
                                                 "first_day": "2026-04-06", "last_day": "2026-04-17"},
                    follow_redirects=False)
    assert r.status_code == 303
    client.post("/settings/holidays", data={"csrf_token": tok, "region": "DE:BY", "as_weekday": "6"})
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        pol = _policy(ws)
        rules = {r.weekday: r.quota_minutes for r in pol.children[0].rules}
        # Mi 1.4. normal (60), Do 2.4. normal, Fr 3.4. Karfreitag = Sonntag normal (60),
        # Sa/So normal (60), Mo 6.4. Ostermontag in den Ferien = Ferien-Sonntag (360), Di 7.4. Ferien (300)
        assert rules == {0: 360, 1: 300, 2: 60, 3: 60, 4: 60, 5: 60, 6: 60}
        with SessionLocal() as db:
            assert policy.day_status(db, db.get(Child, cid), date(2026, 4, 7)).allowed_minutes == 300


def test_midnight_refresh_sends_only_changed(client, parent, today):
    today(date(2026, 4, 1))
    cid, tok = _setup(client, parent)
    _vacation_rules(client, cid, tok)
    client.post("/settings/vacations", data={"csrf_token": tok, "first_day": "2026-04-08", "last_day": "2026-04-17"})
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        pol = _policy(ws)
        ws.send_text(p.PolicyAck(version=pol.version).model_dump_json())
        ws.send_text(p.Ping().model_dump_json())
        from conftest import recv
        assert isinstance(recv(ws), p.Pong)
        assert client.portal.call(vac.refresh_online) == 0                   # nichts geändert
        today(date(2026, 4, 2))                                              # Tageswechsel: Mi 8.4. jetzt im Fenster
        assert client.portal.call(vac.refresh_online) == 1
        new = _policy(ws)
        assert new.version != pol.version
        assert {r.weekday: r.quota_minutes for r in new.children[0].rules}[2] == 300


# ---------- Gutschrift prüft gegen die geltende Regel ----------

def test_credit_window_end_checks_vacation_rule(client, parent, today):
    today(date(2026, 4, 1))
    cid, tok = _setup(client, parent)
    _vacation_rules(client, cid, tok)            # Ferien bis 21:00, normal bis 20:00
    client.post("/settings/vacations", data={"csrf_token": tok, "first_day": "2026-04-01", "last_day": "2026-04-03"})
    r = client.post(f"/children/{cid}/credit", data={"csrf_token": tok, "day": "2026-04-01", "window_end": "20:30"})
    assert r.status_code == 400 and "21:00" in r.text


# ---------- WebUI ----------

def test_settings_vacations_and_region(client, parent, today):
    today(date(2026, 11, 23))
    cid, tok = _setup(client, parent)
    page = client.get("/settings")
    assert "Ferien &amp; Feiertage" in page.text and "Bayern" in page.text and "Wien" in page.text
    assert client.post("/settings/vacations", data={"csrf_token": tok, "first_day": "2026-12-24",
                                                    "last_day": "2026-12-20"}).status_code == 400
    assert client.post("/settings/vacations", data={"csrf_token": tok, "first_day": "2026-11-01",
                                                    "last_day": "2026-11-02"}).status_code == 400   # vergangen
    assert client.post("/settings/vacations", data={"csrf_token": tok, "first_day": "2026-01-01",
                                                    "last_day": "2027-12-31"}).status_code == 400   # zu lang
    client.post("/settings/vacations", data={"csrf_token": tok, "name": "Weihnachtsferien",
                                             "first_day": "2026-12-23", "last_day": "2027-01-06"})
    page = client.get("/settings")
    assert "Weihnachtsferien" in page.text
    assert client.post("/settings/holidays", data={"csrf_token": tok, "region": "DE:XX"}).status_code == 400
    client.post("/settings/holidays", data={"csrf_token": tok, "region": "AT:9", "as_weekday": "5"})
    with SessionLocal() as db:
        region = db.scalar(select(HolidayRegion))
        assert (region.country, region.subdiv, region.as_weekday) == ("AT", "9", 5)
        vid = db.scalar(select(Vacation.id))
    assert 'value="AT:9" selected' in client.get("/settings").text
    client.post("/settings/holidays", data={"csrf_token": tok, "region": ""})
    with SessionLocal() as db:
        assert db.scalar(select(HolidayRegion)) is None
    client.post(f"/settings/vacations/{vid}/delete", data={"csrf_token": tok})
    with SessionLocal() as db:
        assert db.scalar(select(Vacation)) is None


def test_settings_csrf_required(client, parent):
    _setup(client, parent)
    assert client.post("/settings/vacations", data={"first_day": "2030-01-01",
                                                    "last_day": "2030-01-02"}).status_code == 403
    assert client.post("/settings/holidays", data={"region": "DE:"}).status_code == 403


def test_vacation_template_page_and_save(client, parent, today):
    today(date(2026, 4, 1))
    cid, tok = _setup(client, parent)
    page = client.get(f"/children/{cid}/zeiten")
    assert "Ferienvorlage" in page.text and "Noch nicht gespeichert" in page.text
    assert _vacation_rules(client, cid, tok, quota=99999).status_code == 400
    assert _vacation_rules(client, cid, tok).status_code == 303
    with SessionLocal() as db:
        rules = {r.weekday: r.quota_minutes for r in db.get(Child, cid).vacation_rules}
        assert rules == {0: 300, 1: 300, 2: 300, 3: 300, 4: 300, 5: 360, 6: 360}
        assert {r.quota_minutes for r in db.get(Child, cid).rules} == {60}   # Wochenvorlage unverändert
    client.post("/settings/holidays", data={"csrf_token": tok, "region": "DE:BY", "as_weekday": "6"})
    page = client.get(f"/children/{cid}/zeiten")
    assert "Noch nicht gespeichert" not in page.text and "Feiertag: Karfreitag" in page.text
    assert "Feiertag: Karfreitag" not in client.get(f"/children/{cid}").text
    today(date(2026, 4, 3))
    assert "Feiertag: Karfreitag" in client.get(f"/children/{cid}").text


def test_other_family_cannot_touch_vacation(client, parent, today):
    today(date(2026, 4, 1))
    _cid, tok = _setup(client, parent)
    other = add_family("Andere")
    with family_session(other) as db:
        db.add(Vacation(name="Fremd", first_day=date(2026, 5, 1), last_day=date(2026, 5, 2)))
        db.add(HolidayRegion(country="CH", subdiv="ZH", as_weekday=6))
        db.commit()
        vid = db.scalar(select(Vacation.id))
    assert "Fremd" not in client.get("/settings").text
    assert client.post(f"/settings/vacations/{vid}/delete", data={"csrf_token": tok}).status_code == 404
    client.post("/settings/holidays", data={"csrf_token": tok, "region": "DE:BY"})
    with family_session(other) as db:
        assert db.scalar(select(Vacation)) is not None
        assert db.scalar(select(HolidayRegion)).country == "CH"


def test_vacation_rules_deleted_with_child(client, parent):
    cid, tok = _setup(client, parent)
    _vacation_rules(client, cid, tok)
    client.post(f"/children/{cid}/delete", data={"csrf_token": tok})
    with SessionLocal() as db:
        assert db.scalar(select(VacationRule)) is None


def test_family_export_roundtrip_includes_holidays(client, parent, today):
    from app import family_export
    today(date(2026, 4, 1))
    cid, tok = _setup(client, parent)
    _vacation_rules(client, cid, tok)
    client.post("/settings/vacations", data={"csrf_token": tok, "name": "Ostern",
                                             "first_day": "2026-04-06", "last_day": "2026-04-17"})
    client.post("/settings/holidays", data={"csrf_token": tok, "region": "DE:BY", "as_weekday": "5"})
    with SessionLocal() as db:
        data = family_export.export_zip(db)
    other = add_family("Ziel")
    with family_session(other) as db:
        db.add(HolidayRegion(country="CH", subdiv="", as_weekday=6))   # vorher schon eingestellt
        db.commit()
        family_export.import_zip(db, data)
        db.commit()
        assert db.scalar(select(Vacation)).name == "Ostern"
        assert db.scalar(select(HolidayRegion)).country == "DE"
        assert len(db.scalar(select(Child)).vacation_rules) == 7


def test_special_days_grouped():
    region = HolidayRegion(country="DE", subdiv="BY", as_weekday=6)
    cal = _cal([("Ostern", date(2026, 3, 30), date(2026, 4, 10)), ("", date(2026, 4, 13), date(2026, 4, 14))], region)
    assert cal.special_days(date(2026, 4, 1)) == [
        (date(2026, 4, 1), date(2026, 4, 2), "Ostern", None),
        (date(2026, 4, 3), date(2026, 4, 3), "Ostern", "Karfreitag"),
        (date(2026, 4, 4), date(2026, 4, 5), "Ostern", None),
        (date(2026, 4, 6), date(2026, 4, 6), "Ostern", "Ostermontag"),
        (date(2026, 4, 7), date(2026, 4, 10), "Ostern", None),
        (date(2026, 4, 13), date(2026, 4, 14), "", None),
    ]
