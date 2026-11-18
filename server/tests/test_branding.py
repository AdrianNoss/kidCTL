"""Whitelabel: Name, Logo, App-Icon im Verwaltungsbereich; Wirkung auf WebUI, PWA, Mails, Authenticator."""

import io

import pytest
from PIL import Image

from app import branding, mail
from app.config import Settings
from app.models import AdminLog, Branding
from conftest import csrf_from, login, platform_db
from test_operators import admin_login, operator  # noqa: F401  (Fixture)


def _png(w, h, color=(200, 30, 30, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def fresh_brand():
    branding.invalidate()
    yield
    branding.invalidate()


def _save(client, files=None, **data):
    tok = csrf_from(client.get("/admin/branding").text)
    return client.post("/admin/branding", data={"csrf_token": tok, **data}, files=files or {},
                       follow_redirects=False)


def test_default_is_kidctl(client, parent):
    login(client, parent)
    page = client.get("/children").text
    assert "<title>" in page and "KidCTL" in page and "powered by KidCTL" not in page
    m = client.get("/manifest.webmanifest").json()
    assert m["name"] == "KidCTL" and m["icons"][0]["src"].startswith("/brand/icon-192.png")
    assert client.get("/brand/icon-192.png").status_code == 200       # Standard-Icon
    assert client.get("/brand/logo.png").status_code == 404


def test_admin_sets_name_logo_icon(client, operator, parent):  # noqa: F811
    admin_login(client, operator)
    r = _save(client, {"logo": ("logo.png", _png(1024, 256), "image/png"),
                       "icon": ("icon.png", _png(300, 200), "image/png")}, name="  Familien  Wächter ")
    assert r.status_code == 303
    with platform_db() as db:
        row = db.get(Branding, 1)
        assert row.name == "Familien Wächter"
        assert Image.open(io.BytesIO(row.logo)).size == (512, 128)
        assert Image.open(io.BytesIO(row.icon)).size == (512, 512)
        logged = sorted(e.field for e in db.query(AdminLog).filter_by(object="branding"))
    assert logged == ["icon", "logo", "name"]
    b = branding.current()
    assert b.custom and b.has_logo and b.has_icon
    assert Image.open(io.BytesIO(client.get("/brand/icon-180.png").content)).size == (180, 180)
    assert client.get("/brand/icon-77.png").status_code == 404
    assert client.get("/brand/logo.png").headers["content-type"] == "image/png"
    client.cookies.clear()
    login(client, parent)
    page = client.get("/children").text
    assert "Familien Wächter" in page and "powered by KidCTL" in page and 'class="brand-logo"' in page
    assert client.get("/manifest.webmanifest").json()["name"] == "Familien Wächter"
    assert "Familien Wächter" in client.get("/login").text


def test_upload_rejects_non_images_and_support(client, operator):  # noqa: F811
    admin_login(client, operator)
    r = _save(client, {"logo": ("x.png", b"<svg onload=alert(1)>", "image/png")}, name="X")
    assert r.status_code == 400 and "PNG, JPEG" in r.text
    with platform_db() as db:
        row = db.get(Branding, 1)
        assert row is None or row.name == ""                        # nichts gespeichert
    with platform_db() as db:
        from app.models import Operator
        db.get(Operator, operator.id).role = "support"
        db.commit()
    assert client.get("/admin/branding", follow_redirects=False).status_code == 403
    assert "/admin/branding" not in client.get("/admin").text


def test_remove_images_and_logo_only(client, operator):  # noqa: F811
    admin_login(client, operator)
    _save(client, {"logo": ("l.png", _png(100, 40), "image/png")}, name="Wächter", logo_only="1")
    assert branding.current().logo_only
    _save(client, name="Wächter", remove_logo="1", logo_only="1")
    branding.invalidate()
    b = branding.current()
    assert not b.has_logo and not b.logo_only


def test_mail_branding(client, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **kw): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    assert mail.send("a@example.org", "KidCTL: Test", "Hallo von KidCTL")
    assert sent[-1]["Subject"] == "KidCTL: Test" and "powered by" not in sent[-1].get_content()
    with platform_db() as db:
        db.add(Branding(id=1, name="Wächter"))
        db.commit()
    branding.invalidate()
    assert mail.send("a@example.org", "KidCTL: Test", "Hallo von KidCTL")
    body = sent[-1].get_content()
    assert sent[-1]["Subject"] == "Wächter: Test" and "Hallo von Wächter" in body
    assert "Wächter · powered by KidCTL" in body


def test_totp_issuer_uses_brand(client, monkeypatch):
    with platform_db() as db:
        db.add(Branding(id=1, name="Wächter"))
        db.commit()
    branding.invalidate()
    from app.config import settings
    assert settings.totp_issuer == ""                                # ohne TOTP_ISSUER in .env
    assert branding.totp_issuer() == "Wächter"
