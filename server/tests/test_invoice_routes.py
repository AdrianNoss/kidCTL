"""Verwaltungsbereich 2, Etappe 5: Rechnungen über die Oberfläche."""

import io
import re
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import mail, web
from app.config import Settings
from app.models import AdminLog, BillingAddress, Family, Invoice, PriceItem, utcnow
from conftest import SessionLocal, add_family, csrf_from, login, platform_db
from test_admin_roles import _login, _operator
from test_invoices import SENDER


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(Settings, "mail_enabled", property(lambda s: True))
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, attachments=None:
                        sent.append((to, subject, body, attachments or [])) or True)
    return sent


@pytest.fixture
def pushed(monkeypatch):
    from app import policy
    calls = []

    async def fake(db, ids):
        calls.append(set(ids))
    monkeypatch.setattr(policy, "push_policies", fake)
    return calls


def _address():
    with SessionLocal() as db:
        db.add(BillingAddress(name="Maria Muster", street="Hauptstr. 1", postal_code="12345", city="Musterstadt",
                              country="Deutschland", email="rechnung@example.org"))
        db.commit()


def _setup(client):
    tok = csrf_from(client.get("/admin/invoice-settings").text)
    client.post("/admin/invoice-settings", data={"csrf_token": tok, **SENDER, "iban": "de02 1203 0000 0000 2020 51"})
    client.post("/admin/invoice-settings/prices", data={"csrf_token": tok, "label": "KidCTL Jahresabo",
                                                        "amount": "49,00", "months": "12", "active": "1"})
    return tok


def _draft(client, tok):
    with platform_db() as db:
        item = db.scalar(select(PriceItem)).id
    r = client.post("/admin/families/1/invoices", data={"csrf_token": tok, "price_item": str(item)}, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_full_flow(client, parent, saas, outbox, pushed):
    _address()
    with SessionLocal() as db:
        from app.models import Device
        db.add(Device(name="PC", token_hash="q" * 64))
        db.commit()
    with platform_db() as db:
        f = db.get(Family, 1)
        f.plan, f.billing_status, f.trial_ends_at, f.paused_at = "standard", "trial", utcnow() - timedelta(days=30), utcnow()
        db.commit()
    _login(client, _operator("admin"))
    tok = _setup(client)
    with platform_db() as db:
        assert db.get(__import__("app.models", fromlist=["InvoiceSettings"]).InvoiceSettings, 1).iban == "DE02120300000000202051"
        logged = {(e.object, e.field) for e in db.scalars(select(AdminLog))}
    assert ("invoice_settings", "iban") in logged and ("price", "label") in logged
    iid = _draft(client, tok)
    page = client.get(f"/admin/invoices/{iid}").text
    assert 'value="KidCTL Jahresabo"' in page and 'value="49,00"' in page
    r = client.post(f"/admin/invoices/{iid}", data={"csrf_token": tok, "service_start": "2027-01-01",
                                                    "service_end": "2027-12-31",
                                                    "line_text": ["KidCTL Jahresabo", "Treuerabatt", ""],
                                                    "line_qty": ["1", "1", "1"], "line_price": ["49,00", "-4,00", ""]},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "45,00 €" in client.get(f"/admin/invoices/{iid}").text
    r = client.post(f"/admin/invoices/{iid}/issue", data={"csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 303
    with platform_db() as db:
        inv = db.get(Invoice, iid)
        number = inv.number
        assert inv.status == "open" and inv.gross_cents == 4500
    to, subject, body, attachments = outbox[-1]
    assert to == "rechnung@example.org" and number in subject and "DE02120300000000202051" in body
    assert f"Verwendungszweck: {number}" in body and "45,00 €" in body
    assert attachments[0][0] == f"{number}.pdf" and attachments[0][1].startswith(b"%PDF")
    pdf = client.get(f"/admin/invoices/{iid}/pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")

    r = client.post(f"/admin/invoices/{iid}/paid", data={"csrf_token": tok, "paid_on": "2027-01-04"}, follow_redirects=False)
    assert r.status_code == 303
    with platform_db() as db:
        f = db.get(Family, 1)
        assert f.paused_at is None and f.billing_status == "active" and f.paid_until.date().isoformat() == "2027-12-31"
    assert pushed                                                                 # Ruhemodus beendet → Policies

    r = client.post(f"/admin/invoices/{iid}/storno", data={"csrf_token": tok}, follow_redirects=False)
    assert "saved=storno_paid" in r.headers["location"]
    assert "Abo prüfen" in client.get(r.headers["location"]).text
    sid = int(re.search(r"/admin/invoices/(\d+)", r.headers["location"]).group(1))
    with platform_db() as db:
        assert db.get(Invoice, sid).gross_cents == -4500 and db.get(Invoice, iid).status == "canceled"
    n = len(outbox)
    assert client.post(f"/admin/invoices/{iid}/resend", data={"csrf_token": tok}, follow_redirects=False).status_code == 303
    assert len(outbox) == n + 1

    year = inv.year
    csv_text = client.get(f"/admin/invoices/export/{year}.csv").text
    assert number in csv_text and "-45,00" in csv_text
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/admin/invoices/export/{year}.zip").content))
    assert len([n for n in zf.namelist() if n.endswith(".pdf")]) == 2
    listing = client.get("/admin/invoices").text
    assert number in listing and "0,00 €" in listing                             # Summe inkl. Storno

    # Eltern sehen ihre Rechnungen und PDFs
    client.cookies.clear()
    login(client, parent)
    settings_page = client.get("/settings").text
    assert number in settings_page and f"/settings/invoices/{iid}.pdf" in settings_page
    assert client.get(f"/settings/invoices/{iid}.pdf").content.startswith(b"%PDF")


def test_issued_invoice_is_immutable_via_routes(client, saas, outbox):
    _address()
    _login(client, _operator("admin"))
    tok = _setup(client)
    iid = _draft(client, tok)
    client.post(f"/admin/invoices/{iid}/issue", data={"csrf_token": tok})
    with platform_db() as db:
        before = (db.get(Invoice, iid).number, db.get(Invoice, iid).gross_cents)
    r = client.post(f"/admin/invoices/{iid}", data={"csrf_token": tok, "service_start": "2030-01-01",
                                                    "line_text": "Neu", "line_qty": "1", "line_price": "1"})
    assert r.status_code == 409
    assert client.post(f"/admin/invoices/{iid}/delete", data={"csrf_token": tok}).status_code == 409
    assert client.post(f"/admin/invoices/{iid}/issue", data={"csrf_token": tok}).status_code == 400
    with platform_db() as db:
        inv = db.get(Invoice, iid)
        assert (inv.number, inv.gross_cents) == before and [l.text for l in inv.lines] == ["KidCTL Jahresabo"]
    assert "Entwurf bearbeiten" not in client.get(f"/admin/invoices/{iid}").text


def test_issue_blocked_without_address_and_draft_deletable(client, saas):
    _login(client, _operator("admin"))
    tok = _setup(client)
    iid = _draft(client, tok)
    r = client.post(f"/admin/invoices/{iid}/issue", data={"csrf_token": tok})
    assert r.status_code == 400 and "Rechnungsangaben" in r.text
    assert client.post(f"/admin/invoices/{iid}/delete", data={"csrf_token": tok}, follow_redirects=False).status_code == 303
    with platform_db() as db:
        assert db.get(Invoice, iid) is None


@pytest.mark.parametrize("role", ["admin", "support"])
def test_invoice_routes_admin_only(client, saas, role):
    _login(client, _operator(role))
    tok = csrf_from(client.get("/admin/profile").text)
    with platform_db() as db:
        inv = Invoice(family_id=1, status="draft")
        db.add(inv)
        db.commit()
        iid = inv.id
    checks = [client.get("/admin/invoices"), client.get("/admin/invoice-settings"), client.get(f"/admin/invoices/{iid}"),
              client.get("/admin/invoices/export/2027.csv"), client.get("/admin/invoices/export/2027.zip"),
              client.post("/admin/invoice-settings", data={"csrf_token": tok}),
              client.post("/admin/invoice-settings/prices", data={"csrf_token": tok, "label": "x", "amount": "1"}),
              client.post("/admin/families/1/invoices", data={"csrf_token": tok}),
              client.post(f"/admin/invoices/{iid}", data={"csrf_token": tok}),
              client.post(f"/admin/invoices/{iid}/issue", data={"csrf_token": tok}),
              client.post(f"/admin/invoices/{iid}/paid", data={"csrf_token": tok}),
              client.post(f"/admin/invoices/{iid}/storno", data={"csrf_token": tok}),
              client.post(f"/admin/invoices/{iid}/resend", data={"csrf_token": tok}),
              client.get(f"/admin/invoices/{iid}/pdf")]
    if role == "support":
        assert all(r.status_code == 403 for r in checks), [r.status_code for r in checks]
        assert 'href="/admin/invoices"' not in client.get("/admin").text
    else:
        assert all(r.status_code != 403 for r in checks)


def test_other_family_cannot_fetch_pdf(client, parent, saas, outbox):
    _address()
    fid = add_family("Andere")
    with platform_db() as db:
        inv = Invoice(family_id=fid, status="open", number="RE-X", pdf="JVBERi0=")
        db.add(inv)
        db.commit()
        iid = inv.id
    login(client, parent)
    assert client.get(f"/settings/invoices/{iid}.pdf").status_code == 404
