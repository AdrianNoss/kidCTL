"""Internationalisierung: Vollständigkeit des englischen Katalogs, Sprachwahl, Formate, Profil, Umschalter."""

from datetime import date, datetime
from pathlib import Path

from kidctl_common import i18n as core
from kidctl_common.i18n_check import extract_jinja, extract_python, problems, unused

from app.db import SessionLocal
from app.models import Parent
from conftest import csrf_from, login

APP = Path(__file__).resolve().parents[1] / "app"
PO = APP / "locale" / "en.po"


def _msgids():
    files = [f for f in APP.rglob("*.py")]
    return extract_python(files) | extract_jinja(APP / "templates")


def test_every_text_has_english_translation():
    assert problems(PO, _msgids()) == []


def test_no_stale_translations():
    assert unused(PO, _msgids()) == []


def test_language_rules():
    assert core.normalize("de_DE.UTF-8") == "de" and core.normalize("de-AT") == "de"
    assert core.normalize("en_US.UTF-8") == "en" and core.normalize("fr_FR") == "en"
    assert core.normalize("") == "en" and core.normalize("C") == "en" and core.normalize("de:en") == "de"
    assert core.from_accept_language("en-US,en;q=0.9,de;q=0.8") == "en"
    assert core.from_accept_language("fr-FR,de;q=0.9") == "en"                       # bevorzugt: Französisch
    assert core.from_accept_language("de;q=0.7,en;q=0.3") == "de"
    assert core.from_accept_language(None) == "en"
    assert core.from_environ({"LANG": "de_DE.UTF-8", "LC_MESSAGES": "en_GB.UTF-8"}) == "en"
    assert core.from_environ({"LANGUAGE": "de:en", "LANG": "en_US.UTF-8"}) == "de"
    assert core.from_environ({}) is None


def test_formats():
    d, dt = date(2026, 10, 20), datetime(2026, 10, 20, 14, 3)
    assert core.fmt_date(d, with_weekday=True, lang="de") == "Di 20.10.2026"
    assert core.fmt_date(d, with_weekday=True, lang="en") == "Tue 20 Oct 2026"
    assert core.fmt_date(d, year=False, lang="de") == "20.10." and core.fmt_date(d, year=False, lang="en") == "20 Oct"
    assert core.fmt_datetime(dt, lang="de") == "20.10.2026 14:03" and core.fmt_datetime(dt, lang="en") == "20 Oct 2026, 14:03"
    assert [core.fmt_minutes(m, lang="de") for m in (45, 80, 120)] == ["0:45 h", "1:20 h", "2:00 h"]
    assert [core.fmt_minutes(m, lang="en") for m in (45, 80, 120)] == ["45 min", "1 h 20 min", "2 h"]
    assert core.fmt_percent(25, signed=True, lang="de") == "+25 %" and core.fmt_percent(-3, lang="en") == "-3%"


def test_po_parser():
    single, plural = core.parse_po('msgid ""\nmsgstr "Language: en\\n"\n\n#, fuzzy\nmsgid "A"\nmsgstr "B"\n\n'
                                   'msgid "Zeile"\n"n zwei"\nmsgstr "line"\n" two \\"q\\""\n\n'
                                   'msgid "{n} Tag"\nmsgid_plural "{n} Tage"\nmsgstr[0] "{n} day"\nmsgstr[1] "{n} days"\n')
    assert single == {"Zeilen zwei": 'line two "q"'} and plural == {"{n} Tag": ("{n} day", "{n} days")}
    with core.use("en"):
        assert core.ngettext("{n} Apfel", "{n} Äpfel", 2) == "{n} Äpfel"     # nicht im Katalog: unverändert


def test_browser_language_and_switch(client, parent):
    en = {"Accept-Language": "en-US,en;q=0.9"}
    page = client.get("/login", headers=en).text
    assert "Sign in" in page and 'lang="en"' in page and "Forgot password?" in page
    assert "Anmelden" in client.get("/login").text                                   # Test-Client: deutsch
    r = client.get("/lang/de?next=/reset", headers=en, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/reset"
    assert "Passwort vergessen" in client.get("/reset", headers=en).text             # Cookie schlägt Browser
    assert client.get("/lang/en?next=//evil.example", follow_redirects=False).headers["location"] == "/login"


def test_profile_language_and_detected(client, parent):
    client.headers["Accept-Language"] = "en-GB,en;q=0.9"
    login(client, parent)
    with SessionLocal() as db:
        assert db.get(Parent, parent.id).detected_language == "en"
    page = client.get("/profile").text
    assert "Language" in page and "Automatic (browser)" in page
    r = client.post("/profile", data={"csrf_token": csrf_from(page), "username": "mama", "display_name": "",
                                      "email": "", "language": "de"}, follow_redirects=True)
    assert "Profil gespeichert." in r.text and "Sprache" in r.text                    # fest Deutsch trotz Browser
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        assert p.language == "de"
        from app.i18n import parent_language
        assert parent_language(p) == "de"
        p.language = ""
        db.commit()
        assert parent_language(db.get(Parent, parent.id)) == "en"                     # zuletzt erkannt
    assert "Overview" in client.get("/").text


def test_english_pages_render(client, parent):
    client.headers["Accept-Language"] = "en"
    login(client, parent)
    tok = csrf_from(client.get("/children").text)
    client.post("/children", data={"name": "Max", "csrf_token": tok})
    for url, text in [("/", "Overview"), ("/children", "Add child"), ("/children/1", "Weekly schedule"),
                      ("/children/1/stats", "Screen time per day"), ("/children/1/protokoll", "Activity log"),
                      ("/children/1/protokoll?kind=game", "Games"), ("/children/1/chat", "No messages yet."),
                      ("/devices", "Add device"), ("/parents", "Parent accounts"), ("/settings", "Notifications"),
                      ("/profile", "Usage report by email"), ("/profile/report/preview", "Weekly report")]:
        r = client.get(url)
        assert r.status_code == 200 and text in r.text, url
        assert "Bildschirmzeit" not in r.text and "Speichern" not in r.text, url


def test_mail_and_push_in_parent_language(client, parent, monkeypatch):
    from datetime import date as d
    from app import accounts, push, report
    from app.config import Settings
    with SessionLocal() as db:
        p = db.get(Parent, parent.id)
        p.email, p.language = "mama@example.org", "en"
        db.commit()
        subject, text, html = report.render(db, p, report.last_period("weekly", d(2026, 10, 19)))
        assert subject == "KidCTL: Weekly report 12 Oct–18 Oct 2026" and "Hello mama," in text and 'lang="en"' in html
        p.language = ""
        db.commit()
        assert "Wochenbericht 12.10.–18.10.2026" in report.render(db, p, report.last_period("weekly", d(2026, 10, 19)))[0]

        sent = []
        monkeypatch.setattr(accounts.mail, "send", lambda to, subject, body, html=None: sent.append((subject, body)) or True)
        p.detected_language = "en"
        db.commit()
        accounts.start_password_reset(db, "mama")
        assert sent[-1][0] == "KidCTL: reset password" and "a new password was requested" in sent[-1][1]

    payloads = []
    monkeypatch.setattr(Settings, "push_enabled", property(lambda s: True))
    monkeypatch.setattr(push, "_send_one", lambda sub, payload: payloads.append(payload) or True)
    from app.models import PushSubscription
    with SessionLocal() as db:
        db.add(PushSubscription(parent_id=parent.id, endpoint="https://push.example/1", p256dh="x", auth="y"))
        db.commit()
    assert push.send_to_parents_sync(lambda: core.gettext("Gerät „{name}“ offline").format(name="PC"), "x") == 1
    assert '"title": "Device \\u201cPC\\u201d offline"' in payloads[0]
