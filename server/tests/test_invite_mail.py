"""Homepage-Link auf den Anmeldeseiten und Einladungsmail für Einladungscodes."""

import re
from datetime import timedelta

from sqlalchemy import select, text

from conftest import csrf_from, platform_db
from app import branding, operators
from app.models import AdminLog, SignupCode, utcnow

from test_operators import admin_login, operator  # noqa: F401 (Fixture)
from test_signup import _confirm, _link, _mode, _post, outbox  # noqa: F401 (Fixture)


def _homepage(client, **data):
    tok = csrf_from(client.get("/admin/branding").text)
    r = client.post("/admin/branding/homepage", data={"csrf_token": tok, "homepage_url": "", "homepage_label_de": "",
                                                       "homepage_label_en": "", **data}, follow_redirects=False)
    branding.invalidate()
    return r


def test_homepage_link_on_login_pages(client, operator):  # noqa: F811
    page = client.get("/login").text                                                # Standard: Projektseite
    assert 'href="https://kidctl.de/de/"' in page and "Über KidCTL" in page
    admin_login(client, operator)
    assert _homepage(client, homepage_url="http://example.org").status_code == 400   # nur https
    assert _homepage(client, homepage_url="https://").status_code == 400
    assert _homepage(client, homepage_url="https://familie.example.org", homepage_label_de="Zur Startseite",
                     homepage_label_en="Home").status_code == 303
    with platform_db() as db:
        assert db.scalar(select(AdminLog).where(AdminLog.object == "branding", AdminLog.field == "homepage_url")
                         .order_by(AdminLog.id.desc())) is not None                # protokolliert
    anon = client.__class__(client.app, headers={"accept-language": "de"})
    for path in ("/login", "/reset", "/admin/login"):
        page = anon.get(path).text
        assert 'href="https://familie.example.org"' in page and "Zur Startseite" in page, path
    assert "Home</a>" in anon.get("/login", headers={"accept-language": "en"}).text
    _homepage(client)
    assert "https://kidctl.de/" in anon.get("/login").text                        # leer: wieder Standard


def test_invite_mail_and_resend(client, operator, monkeypatch, outbox):  # noqa: F811
    _mode(monkeypatch, "invite")
    admin_login(client, operator)
    tok = csrf_from(client.get("/admin/codes").text)
    page = client.post("/admin/codes", data={"csrf_token": tok, "note": "Familie Weber", "email": "kein-mail"}).text
    assert "E-Mail" in page and not outbox                                          # ungültige Adresse
    page = client.post("/admin/codes", data={"csrf_token": tok, "note": "Familie Weber", "email": "weber@example.org",
                                             "lang": "en", "free": "1"}).text
    code = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})", page).group(1)
    to, subject, body = outbox[-1]
    assert to == "weber@example.org" and subject == "KidCTL: Your invitation"
    assert code in body and f"/signup?code={code}" in body and "Plan: Free." in body
    assert "w***@example.org" in client.get("/admin/codes").text and "weber@example.org" not in page

    with platform_db() as db:
        old = db.scalar(select(SignupCode))
        assert old.email == "weber@example.org" and old.lang == "en"
        raw = db.execute(text("SELECT email FROM signup_code")).scalar()
        assert "weber" not in raw                                                    # Plattform-Schlüssel
    page = client.post(f"/admin/codes/{old.id}/resend", data={"csrf_token": tok}).text
    new = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})", page).group(1)
    assert new != code and new in outbox[-1][2] and outbox[-1][0] == "weber@example.org"
    with platform_db() as db:
        assert operators.find_code(db, code) is None                                 # alter Code ungültig
        row = operators.find_code(db, new)
        assert row.plan == "free" and row.note == "Familie Weber" and row.email == "weber@example.org"

    anon = client.__class__(client.app)
    page = anon.get(f"/signup?code={new}").text                                       # Link aus der Mail
    assert f'value="{new}"' in page and 'value="weber@example.org"' in page
    assert 'value="weber' not in anon.get("/signup?code=AAAA-BBBB-CCCC").text
    r = _post(anon, "/signup", {"email": "andere@example.org", "code": new})          # Adresse änderbar
    assert r.status_code == 200 and outbox[-1][0] == "andere@example.org"             # Bestätigung bleibt
    r, _secret = _confirm(anon, _link(outbox))
    assert r.status_code == 303
    with platform_db() as db:
        used = db.scalar(select(SignupCode).where(SignupCode.used_at.is_not(None)))
        assert used.email is None                                                    # nach Einlösen gelöscht


def test_expired_invite_forgets_address(client):
    with platform_db() as db:
        operators.create_code(db, "x", None, "", "alt@example.org")
        operators.create_code(db, "y", None, "", "offen@example.org")
        row = db.scalar(select(SignupCode).where(SignupCode.note == "x"))
        row.expires_at = utcnow() - timedelta(days=1)
        db.commit()
        from app import retention
        retention.prune_all(db)          # Zählung nicht prüfen: der Start-Lauf im Hintergrund kann zuvorkommen
        assert db.scalar(select(SignupCode).where(SignupCode.note == "x")).email is None
        assert db.scalar(select(SignupCode).where(SignupCode.note == "y")).email == "offen@example.org"
