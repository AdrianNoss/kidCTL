"""Musterrechnungen (beide Steuermodi) für die Prüfung durch den Steuerberater erzeugen – ohne Datenbank.

    KIDCTL_DEV=1 PYTHONPATH=server:common python dev/sample_invoices.py

Schreibt dev/musterrechnung-kleinunternehmer.pdf, dev/musterrechnung-regelbesteuerung.pdf und
dev/musterrechnung-storno.pdf. Absender und Empfänger sind Platzhalter.
"""

import json
from datetime import date
from pathlib import Path

from app import invoices
from app.models import Invoice, InvoiceLine

OUT = Path(__file__).parent
SENDER = {"sender_name": "Max Mustermann (Muster)", "sender_street": "Musterweg 1", "sender_postal_code": "12345",
          "sender_city": "Musterstadt", "sender_country": "Deutschland", "tax_id": "12/345/67890",
          "contact": "rechnung@example.org · kids.example.org", "bank_holder": "Max Mustermann",
          "iban": "DE02120300000000202051", "bic": "BYLADEM1001",
          "footer": "Vielen Dank für dein Vertrauen in KidCTL."}
ADDRESS = {"name": "Erika Musterfrau", "street": "Beispielstraße 7", "postal_code": "54321", "city": "Beispielhausen",
           "country": "Deutschland", "email": "erika@example.org", "vat_id": ""}


def sample(mode: str, number: str, storno_of: int | None = None, sign: int = 1) -> Invoice:
    inv = Invoice(id=1, family_id=1, number=number, year=2027, seq=1, issue_date=date(2027, 1, 4),
                  due_date=date(2027, 1, 18), service_start=date(2027, 1, 1), service_end=date(2027, 12, 31),
                  tax_mode=mode, address=json.dumps(ADDRESS), sender=json.dumps(SENDER), storno_of=storno_of,
                  reference="RE-2027-0001 vom 04.01.2027" if storno_of else "")
    inv.lines = [InvoiceLine(pos=1, text="KidCTL Jahresabo (gehostet)", quantity=1, unit_cents=sign * 4900,
                             total_cents=sign * 4900),
                 InvoiceLine(pos=2, text="Rabatt Freunde & Familie", quantity=1, unit_cents=sign * -900,
                             total_cents=sign * -900)]
    inv.tax_rate, inv.net_cents, inv.tax_cents, inv.gross_cents = invoices.totals(inv.lines, mode)
    return inv


if __name__ == "__main__":
    for name, inv in (("kleinunternehmer", sample("small_business", "RE-2027-0001")),
                      ("regelbesteuerung", sample("standard", "RE-2027-0002")),
                      ("storno", sample("small_business", "RE-2027-0003", storno_of=1, sign=-1))):
        path = OUT / f"musterrechnung-{name}.pdf"
        path.write_bytes(invoices.render_pdf(inv))
        print(f"{path}  {invoices.eur(inv.gross_cents)}")
