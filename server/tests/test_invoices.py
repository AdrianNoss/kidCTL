"""Verwaltungsbereich 2, Etappe 5: Rechnungen – Kernlogik."""

import io
import json
import threading
import zipfile
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import invoices, web
from app.config import Settings
from app.models import (
    AdminLog, BillingAddress, Family, Invoice, InvoiceLine, InvoiceSettings, PriceItem, utcnow,
)
from conftest import SessionLocal, platform_db

ACTOR = ("op-admin", None)
SENDER = dict(sender_name="Adrian Muster", sender_street="Weg 1", sender_postal_code="10115", sender_city="Berlin",
              sender_country="Deutschland", tax_id="12/345/67890", contact="kontakt@example.org",
              bank_holder="Adrian Muster", iban="DE02120300000000202051", bic="BYLADEM1001", footer="Danke!")


@pytest.fixture
def saas(monkeypatch):
    monkeypatch.setattr(Settings, "saas", property(lambda s: True))
    monkeypatch.setitem(web.templates.env.globals, "SAAS", True)


@pytest.fixture
def ready(client, saas):
    with platform_db() as db:
        db.add(InvoiceSettings(id=1, **SENDER))
        db.add(PriceItem(label="KidCTL Jahresabo", amount_cents=4900, months=12))
        db.commit()
    with SessionLocal() as db:
        db.add(BillingAddress(name="Maria Muster", street="Hauptstr. 1", postal_code="12345", city="Musterstadt",
                              country="Deutschland", email="rechnung@example.org"))
        db.commit()


def _draft(db, cents=4900, qty=1):
    inv = invoices.create_draft(db, db.get(Family, 1), None, ACTOR)
    invoices.update_draft(db, inv, date(2027, 1, 1), date(2027, 12, 31), [("Abo", qty, cents)])
    return inv


def test_money():
    assert invoices.eur(123456789) == "1.234.567,89 €" and invoices.eur(-5) == "-0,05 €"
    for text, cents in (("12,50", 1250), ("12.5", 1250), ("-5", -500), ("1.234,56", 123456), ("0,01", 1)):
        assert invoices.parse_eur(text) == cents
    for bad in ("", "1,234", "abc", "12,5x"):
        with pytest.raises(ValueError):
            invoices.parse_eur(bad)


def test_tax_modes_and_rounding():
    lines = [InvoiceLine(total_cents=t) for t in (4900, 1, 0)]          # 49,01 €
    assert invoices.totals(lines, "small_business") == (0, 4901, 0, 4901)
    assert invoices.totals(lines, "standard") == (19, 4901, 931, 5832)      # 931,19 → 931
    assert invoices.vat(50) == 10 and invoices.vat(-50) == -10              # 9,5 → 10 (halb vom Nullpunkt weg)
    assert invoices.vat(4900) == 931 and invoices.vat(-4900) == -931


def test_draft_prefill_from_price_list(ready):
    with platform_db() as db:
        f = db.get(Family, 1)
        f.paid_until = utcnow() + timedelta(days=10)
        db.commit()
        item = db.scalar(select(PriceItem))
        inv = invoices.create_draft(db, f, item, ACTOR)
        assert inv.service_start == f.paid_until.date() + timedelta(days=1)
        assert inv.service_end == invoices._add_months(inv.service_start, 12) - timedelta(days=1)
        assert [(l.text, l.total_cents) for l in inv.lines] == [("KidCTL Jahresabo", 4900)]
        assert invoices._add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)


def test_issue_freezes_and_numbers_are_gapless(ready, monkeypatch):
    with platform_db() as db:
        a, b = _draft(db), _draft(db, cents=1000, qty=3)
        invoices.issue(db, a, ACTOR)
        invoices.issue(db, b, ACTOR)
        year = invoices.today().year
        assert (a.number, b.number) == (f"RE-{year}-0001", f"RE-{year}-0002") and (a.seq, b.seq) == (1, 2)
        assert a.gross_cents == 4900 and a.tax_mode == "small_business" and b.gross_cents == 3000
        assert json.loads(a.address)["name"] == "Maria Muster" and json.loads(a.sender)["iban"].startswith("DE02")
        assert invoices.pdf_bytes(a).startswith(b"%PDF")
        # eingefroren: spätere Änderungen an Adresse/Einstellungen/Modus wirken nicht mehr
        db.scalar(select(BillingAddress)).name = "Neu"
        db.get(InvoiceSettings, 1).iban = "DE00"
        db.commit()
        monkeypatch.setattr(Settings, "invoice_tax_mode", property(lambda s: "standard"))
        db.refresh(a)
        assert json.loads(a.address)["name"] == "Maria Muster" and a.tax_mode == "small_business"
        with pytest.raises(invoices.InvoiceError):
            invoices.update_draft(db, a, None, None, [("x", 1, 1)])
        with pytest.raises(invoices.InvoiceError):
            invoices.delete_draft(db, a, ACTOR)
        with pytest.raises(invoices.InvoiceError):
            invoices.issue(db, a, ACTOR)
        c = _draft(db)
        invoices.issue(db, c, ACTOR)
        assert c.tax_mode == "standard" and (c.net_cents, c.tax_cents, c.gross_cents) == (4900, 931, 5831)


def test_concurrent_issuing_is_gapless(ready):
    with platform_db() as db:
        ids = [_draft(db).id for _ in range(6)]
    barrier, errors = threading.Barrier(len(ids)), []

    def work(iid):
        try:
            with platform_db() as db:
                inv = db.get(Invoice, iid)
                barrier.wait()
                invoices.issue(db, inv, ACTOR)
        except Exception as e:   # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=work, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with platform_db() as db:
        assert sorted(db.scalars(select(Invoice.seq).where(Invoice.id.in_(ids)))) == list(range(1, 7))


def test_issue_requires_settings_and_address(client, saas):
    with platform_db() as db:
        inv = _draft(db)
        problems = invoices.issue_problems(db, inv)
        assert any("Einstellungen" in p for p in problems) and any("Rechnungsangaben" in p for p in problems)
        with pytest.raises(invoices.InvoiceError):
            invoices.issue(db, inv, ACTOR)
        assert inv.number is None


def test_paid_couples_subscription(ready, monkeypatch):
    from app import billing
    with platform_db() as db:
        f = db.get(Family, 1)
        f.plan, f.billing_status, f.trial_ends_at, f.paused_at = "standard", "trial", utcnow() - timedelta(days=40), utcnow()
        db.commit()
        inv = _draft(db)
        invoices.issue(db, inv, ACTOR)
        assert invoices.mark_paid(db, inv, date(2027, 1, 5), ACTOR) == 1
        f = db.get(Family, 1)
        assert f.billing_status == "active" and f.paid_until.date() == date(2027, 12, 31)
        assert billing.check_family(db, 1) == "resumed" and db.get(Family, 1).paused_at is None
        with pytest.raises(invoices.InvoiceError):
            invoices.mark_paid(db, inv, date(2027, 1, 6), ACTOR)
        # späteres „bezahlt bis“ wird nicht verkürzt
        f.paid_until = f.paid_until + timedelta(days=400)
        db.commit()
        other = _draft(db)
        invoices.issue(db, other, ACTOR)
        invoices.mark_paid(db, other, date(2027, 2, 1), ACTOR)
        assert db.get(Family, 1).paid_until.date() == date(2029, 2, 3)


def test_storno(ready):
    with platform_db() as db:
        inv = _draft(db, cents=4900, qty=2)
        invoices.issue(db, inv, ACTOR)
        invoices.mark_paid(db, inv, date(2027, 1, 5), ACTOR)
        paid_until = db.get(Family, 1).paid_until
        s = invoices.storno(db, inv, ACTOR)
        assert s.storno_of == inv.id and s.status == "storno" and s.gross_cents == -9800 and s.seq == inv.seq + 1
        assert inv.status == "canceled" and inv.gross_cents == 9800                  # Original bleibt
        assert s.reference == f"{inv.number} vom {inv.issue_date:%d.%m.%Y}"
        assert db.get(Family, 1).paid_until == paid_until                            # Abo unverändert
        log = [e.new for e in db.scalars(select(AdminLog).where(AdminLog.object == "invoice", AdminLog.field == "storniert"))]
        assert "Abo prüfen" in log[0]
        with pytest.raises(invoices.InvoiceError):
            invoices.storno(db, inv, ACTOR)
        assert invoices.pdf_bytes(s).startswith(b"%PDF")


def test_family_deletion_keeps_issued_invoices(ready):
    from app.tenant import delete_family
    with platform_db() as db:
        db.get(Family, 1).name = "Familie Muster"
        db.commit()
        issued, draft = _draft(db), _draft(db)
        invoices.issue(db, issued, ACTOR)
        delete_family(db, 1)
        assert db.get(Invoice, draft.id) is None
        kept = db.get(Invoice, issued.id)
        assert kept is not None and kept.family_label == "Familie Muster" and invoices.pdf_bytes(kept).startswith(b"%PDF")
        assert json.loads(kept.address)["city"] == "Musterstadt"


def test_retention_and_export(ready):
    with platform_db() as db:
        inv = _draft(db)
        invoices.issue(db, inv, ACTOR)
        year = inv.year
        csv_text = invoices.export_csv(db, year)
        assert inv.number in csv_text and "49,00" in csv_text and "Musterstadt" not in csv_text
        zf = zipfile.ZipFile(io.BytesIO(invoices.export_zip(db, year)))
        assert sorted(zf.namelist()) == sorted([f"rechnungen-{year}.csv", f"{inv.number}.pdf"])
        assert invoices.expires_on(inv) == date(year + 8, 12, 31)
        assert invoices.purge_expired(db, date(year + 8, 12, 31)) == 0
        assert invoices.purge_expired(db, date(year + 9, 1, 1)) == 1
        assert db.get(Invoice, inv.id) is None
        assert db.scalar(select(AdminLog).where(AdminLog.object == "invoice", AdminLog.object_id == inv.id)) is None
