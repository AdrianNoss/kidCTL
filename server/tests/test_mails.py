"""Einheitliche HTML-Mails: jeder Mailtyp in DE und EN, Logo per CID, Textfassung, keine externen Ressourcen,
Whitelabel an/aus, Fußzeile mit Anbieter-Angaben."""

import re

import pytest

from app import branding, i18n, mail, mailer
from app.config import Settings
from app.models import Branding
from conftest import platform_db

TYPES = sorted(mailer.samples())


@pytest.fixture(autouse=True)
def fresh_brand():
    branding.invalidate()
    mailer._logo_cache.clear()
    yield
    branding.invalidate()
    mailer._logo_cache.clear()


def _check(r, lang):
    assert r.subject and r.text.strip() and r.html.startswith("<!doctype html>")
    assert len(r.html.encode()) < mailer.MAX_HTML                                 # Gmail kürzt ab ~102 KB
    assert "cid:" + mailer.LOGO_CID in r.html
    assert not re.search(r"<script|<link|<iframe|src=\"https?:|url\(", r.html, re.I)   # nichts nachladen
    for part in (r.subject, r.text, r.html):
        assert not re.search(r"\{[a-z_]+\}", part), part                     # keine offenen Platzhalter
        assert "None" not in re.sub(r"<[^>]+>", "", part)


@pytest.mark.parametrize("name", TYPES)
@pytest.mark.parametrize("lang", ["de", "en"])
def test_every_mail_type_renders(client, name, lang):
    with i18n.use(lang):
        ctx = mailer.samples()[name]
    r = mailer.render(name, lang, **ctx)
    _check(r, lang)
    assert r.subject.startswith("KidCTL: ") != (name in ("newsletter", "feature_request")) and "powered by" not in r.html
    if lang == "en" and name not in ("invoice", "ticket_support", "operator_notice"):
        assert "Hallo" not in r.html and "Du bekommst" not in r.text          # wirklich übersetzt


def test_whitelabel_and_provider_footer(client, monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    with platform_db() as db:
        db.add(Branding(id=1, name="Wächter", provider_name="Beispiel GmbH", provider_address="Weg 1, 12345 Ort",
                        provider_email="hallo@example.org", imprint_url="https://example.org/impressum",
                        privacy_url="", news_url=""))
        db.commit()
    branding.invalidate()
    r = mailer.render("password_reset", "de", **mailer.samples()["password_reset"])
    assert r.subject == "Wächter: Passwort zurücksetzen" and "Wächter-Account" in r.html
    assert "powered by KidCTL" in r.html and "Wächter · powered by KidCTL" in r.text
    assert "Beispiel GmbH · Weg 1, 12345 Ort · hallo@example.org" in r.html
    assert 'href="https://example.org/impressum"' in r.html and "Datenschutz" not in r.html   # leer: entfällt
    assert "/support" in r.html and "kidctl.de/de/news/" in r.html


def test_single_mode_footer_without_support(client):
    r = mailer.render("email_verify", "en", **mailer.samples()["email_verify"])
    assert "/support" not in r.html and "kidctl.de/news/" in r.text and "Imprint" not in r.html


def test_send_uses_cid_logo_and_alternative(client, monkeypatch):
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
    assert mailer.send("a@example.org", "invoice", "de", attachments=[("r.pdf", b"%PDF", "application/pdf")],
                       **mailer.samples()["invoice"])
    msg = sent[-1]
    types = [p.get_content_type() for p in msg.walk()]
    assert types[:5] == ["multipart/mixed", "multipart/alternative", "text/plain", "multipart/related", "text/html"]
    assert "image/png" in types and "application/pdf" in types
    img = next(p for p in msg.walk() if p.get_content_type() == "image/png")
    assert img["Content-ID"] == f"<{mailer.LOGO_CID}>"
    text = next(p for p in msg.walk() if p.get_content_type() == "text/plain").get_content()
    assert "IBAN: DE02 1203 0000 0000 2020 51" in text and "Verwendungszweck: 2026-0001" in text


# ---------- Verwaltungsbereich: Anbieter, Mail-Vorschau ----------

from app.models import AdminLog, Operator  # noqa: E402
from conftest import csrf_from  # noqa: E402
from test_operators import admin_login, operator  # noqa: E402,F401  (Fixture)


def test_provider_settings(client, operator):  # noqa: F811
    admin_login(client, operator)
    tok = csrf_from(client.get("/admin/branding").text)
    data = {"csrf_token": tok, "provider_name": "Beispiel  GmbH", "provider_address": "Weg 1,\n12345 Ort",
            "provider_email": "hallo@example.org", "imprint_url": "https://example.org/impressum",
            "privacy_url": "", "news_url": ""}
    assert client.post("/admin/branding/provider", data={**data, "imprint_url": "javascript:alert(1)"}).status_code == 400
    assert client.post("/admin/branding/provider", data={**data, "provider_email": "kaputt"}).status_code == 400
    r = client.post("/admin/branding/provider", data=data, follow_redirects=False)
    assert r.status_code == 303
    with platform_db() as db:
        row = db.get(Branding, 1)
        assert row.provider_name == "Beispiel GmbH" and row.provider_address == "Weg 1, 12345 Ort"
        assert {e.field for e in db.query(AdminLog).filter_by(object="branding")} >= {"provider_name", "imprint_url"}
    assert "Beispiel GmbH · Weg 1, 12345 Ort" in mailer.render("invite", "de", **mailer.samples()["invite"]).html


def test_mail_preview_and_test_send(client, operator, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, **kw: sent.append((to, subject, kw)) or True)
    admin_login(client, operator)
    r = client.get("/admin/mail-preview?type=invoice&lang=en")
    page = r.text
    nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    assert f"nonce=&#34;{nonce}&#34;" in page and " style=&#34;" not in page        # Rahmen erbt die CSP der Seite
    assert "srcdoc=" in page and "Invoice 2026-0001" in page and "data:image/png;base64," in page and "cid:" not in page
    assert "cid:" not in page
    tok = csrf_from(page)
    r = client.post("/admin/mail-preview/send", data={"csrf_token": tok, "type": "invoice", "lang": "en"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("sent=noaddr") and not sent
    with platform_db() as db:
        db.get(Operator, operator.id).email = "op@example.org"
        db.commit()
    r = client.post("/admin/mail-preview/send", data={"csrf_token": tok, "type": "report", "lang": "de"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("sent=1") and sent[-1][0] == "op@example.org"
    assert sent[-1][2]["inline"][0][0] == mailer.LOGO_CID
    assert client.get("/admin/mail-preview?type=gibtsnicht").status_code == 200        # Rückfall auf den ersten Typ
    with platform_db() as db:
        db.get(Operator, operator.id).role = "support"
        db.commit()
    assert client.get("/admin/mail-preview", follow_redirects=False).status_code == 403
    assert client.post("/admin/branding/provider", data={"csrf_token": tok}).status_code == 403


# ---------- Newsletter: Markdown-Teilmenge ----------

def test_markdown_subset_is_safe():
    from app import mdmail
    html = mdmail.to_html("# Titel\n\n### Unter\n\n**fett** *kursiv*\n\n- a\n- b\n\n1. eins\n\n---\n\n"
                          "[gut](https://example.org) [mail](mailto:a@b.de) [böse](javascript:alert(1))\n\n"
                          "<script>alert(1)</script> ![bild](https://x/y.png)\n\n> Zitat\n\n    code")
    assert "<h2 " in html and "<h3 " in html and "<h1" not in html
    assert "<strong>fett</strong>" in html and "<em>kursiv</em>" in html and "<ul " in html and "<ol " in html
    assert "<hr " in html and 'href="https://example.org"' in html and 'href="mailto:a@b.de"' in html
    assert "javascript:" not in html.replace("[böse](javascript:alert(1))", "")   # nur als Text, nie als Link
    assert "<script" not in html and "&lt;script&gt;" in html and "<img" not in html
    assert "<blockquote" not in html and "<pre" not in html and "<code" not in html
    assert mdmail.preheader("## Neu\n\n**Hallo** Welt") == "Neu"
