"""Rechnungen (Verwaltungsbereich 2, Etappe 5): manueller Generator nach § 14 UStG, bis über Merchant of Record bzw.
Zahlungsdienstleister entschieden ist.

- **Beträge** immer als ganze Cent. Position = Menge × Einzelpreis (exakt). Regelbesteuerung: Preise sind netto,
  die Umsatzsteuer wird **einmal auf die Nettosumme** berechnet und kaufmännisch gerundet (halbe Cent vom Nullpunkt
  weg); brutto = netto + USt. Kleinunternehmer (§ 19 UStG): keine USt, Summe = Rechnungsbetrag.
- **Lebenszyklus:** Entwurf (änderbar, löschbar, ohne Nummer) → Ausstellen (Nummer aus dem Jahres-Nummernkreis unter
  Zeilensperre, Datum, Kopie von Rechnungsadresse und Absender, Beträge und Steuermodus eingefroren, PDF) → offen →
  bezahlt. Korrektur nur per Storno-Rechnung (eigene Nummer, Bezug, negative Beträge); das Original bleibt.
- **Kopplung mit dem Abo:** „bezahlt“ setzt das Abo aktiv und „bezahlt bis“ auf das Ende des Leistungszeitraums.
- **Archiv:** Adresse, Absender und PDF mit dem Plattform-Schlüssel; Rechnungen überleben das Löschen der Familie und
  werden erst nach der Aufbewahrungsfrist gelöscht (8 Jahre ab Ende des Ausstellungsjahres, § 14b UStG, § 147 AO).
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import audit
from .config import settings
from .i18n import N_
from .models import (
    AdminLog, BillingAddress, Family, Invoice, InvoiceCounter, InvoiceLine, InvoiceSettings, PriceItem, utcnow,
)

log = logging.getLogger("kidctl.invoices")

VAT_RATE = 19
RETENTION_YEARS = 8
SMALL_BUSINESS_NOTE = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."
STATUSES = {"draft": N_("Entwurf"), "open": N_("offen"), "paid": N_("bezahlt"), "canceled": N_("storniert"),
            "storno": N_("Storno")}
ISSUED = ("open", "paid", "canceled", "storno")


class InvoiceError(Exception):
    pass


# ---------- Geld ----------

def eur(cents: int) -> str:
    """1234567 → „12.345,67 €“."""
    sign = "-" if cents < 0 else ""
    euros, rest = divmod(abs(cents), 100)
    return f"{sign}{euros:,}".replace(",", ".") + f",{rest:02d} €"


def parse_eur(text: str) -> int:
    """„12,50“, „12.50“, „-5“, „1.234,56“ → Cent. Fehler: ValueError."""
    t = text.strip().replace("€", "").replace(" ", "")
    if not t:
        raise ValueError("leer")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    neg = t.startswith("-")
    t = t.lstrip("+-")
    whole, _sep, frac = t.partition(".")
    if (whole and not whole.isdigit()) or (frac and (not frac.isdigit() or len(frac) > 2)):
        raise ValueError(text)
    cents = int(whole or 0) * 100 + int((frac + "00")[:2] or 0)
    return -cents if neg else cents


def vat(net_cents: int, rate: int = VAT_RATE) -> int:
    """USt auf die Nettosumme, kaufmännisch gerundet (halbe Cent vom Nullpunkt weg)."""
    tax = (abs(net_cents) * rate * 2 + 100) // 200
    return -tax if net_cents < 0 else tax


def totals(lines: list[InvoiceLine], mode: str) -> tuple[int, int, int, int]:
    """(Steuersatz, netto, USt, brutto) in Cent."""
    net = sum(line.total_cents for line in lines)
    if mode == "standard":
        tax = vat(net)
        return VAT_RATE, net, tax, net + tax
    return 0, net, 0, net


# ---------- Einstellungen, Preisliste ----------

def settings_row(db: Session) -> InvoiceSettings:
    row = db.get(InvoiceSettings, 1)
    if row is None:
        row = InvoiceSettings(id=1)
        db.add(row)
        db.flush()
    return row


def missing_settings(row: InvoiceSettings) -> list[str]:
    return [f for f in InvoiceSettings.REQUIRED if not (getattr(row, f) or "").strip()]


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


# ---------- Entwurf ----------

def create_draft(db: Session, family: Family, item: PriceItem | None, actor) -> Invoice:
    start = today()
    if family.paid_until is not None and family.paid_until.date() >= start:
        start = family.paid_until.date() + timedelta(days=1)          # Tag nach „bezahlt bis“
    inv = Invoice(family_id=family.id, status="draft", service_start=start)
    if item is not None:
        inv.service_end = _add_months(start, item.months) - timedelta(days=1)
        inv.lines.append(InvoiceLine(pos=1, text=item.label, quantity=1, unit_cents=item.amount_cents,
                                     total_cents=item.amount_cents))
    db.add(inv)
    db.flush()
    audit.record(db, actor, "invoice", "Entwurf", None, item.label if item else "–", family_id=family.id,
                 object_id=inv.id)
    db.commit()
    return inv


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def _lock(db: Session, inv: Invoice) -> None:
    """Rechnungszeile bis zum Commit sperren und den aktuellen Stand neu laden, damit Statusprüfungen nicht auf
    einem veralteten Stand derselben Rechnung arbeiten."""
    if inv.id is not None:
        db.refresh(inv, with_for_update=True)


def update_draft(db: Session, inv: Invoice, start: date | None, end: date | None,
                 lines: list[tuple[str, int, int]]) -> None:
    """Entwurf speichern; ``lines`` = (Text, Menge, Einzelpreis in Cent)."""
    _lock(db, inv)
    if inv.status != "draft":
        raise InvoiceError("Ausgestellte Rechnungen sind unveränderlich – Korrektur nur per Storno.")
    if start and end and end < start:
        raise InvoiceError("Der Leistungszeitraum endet vor seinem Beginn.")
    inv.service_start, inv.service_end = start, end
    inv.lines.clear()
    db.flush()
    for pos, (text, qty, unit) in enumerate(lines, start=1):
        inv.lines.append(InvoiceLine(pos=pos, text=text[:255], quantity=qty, unit_cents=unit, total_cents=qty * unit))
    db.commit()


def delete_draft(db: Session, inv: Invoice, actor) -> None:
    _lock(db, inv)
    if inv.status != "draft":
        raise InvoiceError("Ausgestellte Rechnungen lassen sich nicht löschen – Korrektur nur per Storno.")
    audit.record(db, actor, "invoice", "Entwurf gelöscht", None, None, family_id=inv.family_id, object_id=inv.id)
    db.delete(inv)
    db.commit()


# ---------- Ausstellen ----------

def issue_problems(db: Session, inv: Invoice) -> list[str]:
    """Was fehlt zum Ausstellen (leer = bereit)."""
    problems = []
    if inv.status != "draft":
        problems.append("Nur Entwürfe lassen sich ausstellen.")
    if missing_settings(settings_row(db)):
        problems.append("Rechnungs-Einstellungen unvollständig (Absender, Steuernummer, Kontakt, Bankverbindung).")
    address = db.scalar(select(BillingAddress).where(BillingAddress.family_id == inv.family_id))
    if inv.storno_of is None and (address is None or not address.complete):
        problems.append("Die Familie hat keine vollständigen Rechnungsangaben.")
    if not inv.lines:
        problems.append("Die Rechnung hat keine Positionen.")
    if not (inv.service_start and inv.service_end):
        problems.append("Bitte den Leistungszeitraum angeben.")
    return problems


def _next_number(db: Session, year: int) -> tuple[int, str]:
    """Nächste Nummer des Jahres – Zeilensperre bis zum Commit derselben Transaktion: lückenlos und eindeutig auch
    bei gleichzeitigem Ausstellen."""
    db.execute(pg_insert(InvoiceCounter).values(year=year, last=0).on_conflict_do_nothing(index_elements=["year"]))
    counter = db.execute(select(InvoiceCounter).where(InvoiceCounter.year == year).with_for_update()).scalar_one()
    counter.last += 1
    number = settings.invoice_number_format.replace("{YYYY}", f"{year:04d}").replace("{NNNN}", f"{counter.last:04d}")
    return counter.last, number


def _address_json(db: Session, inv: Invoice) -> str:
    a = db.scalar(select(BillingAddress).where(BillingAddress.family_id == inv.family_id))
    fields = ("name", "street", "postal_code", "city", "country", "email", "vat_id")
    return json.dumps({f: getattr(a, f) for f in fields} if a else {}, ensure_ascii=False)


def _sender_json(row: InvoiceSettings) -> str:
    fields = ("sender_name", "sender_street", "sender_postal_code", "sender_city", "sender_country", "tax_id",
              "contact", "bank_holder", "iban", "bic", "footer")
    return json.dumps({f: getattr(row, f) for f in fields}, ensure_ascii=False)


def issue(db: Session, inv: Invoice, actor, address_json: str | None = None) -> Invoice:
    """Ausstellen: Nummer, Datum, Kopien und Beträge einfrieren, PDF erzeugen – alles in einer Transaktion."""
    _lock(db, inv)
    if problems := issue_problems(db, inv):
        raise InvoiceError(" ".join(problems))
    row = settings_row(db)
    day = today()
    inv.seq, inv.number = _next_number(db, day.year)
    inv.year, inv.issue_date = day.year, day
    inv.due_date = day + timedelta(days=settings.invoice_due_days)
    inv.tax_mode = settings.invoice_tax_mode
    inv.tax_rate, inv.net_cents, inv.tax_cents, inv.gross_cents = totals(inv.lines, inv.tax_mode)
    family = db.get(Family, inv.family_id)
    inv.family_label = (family.name if family else "") or ""
    inv.address = address_json or _address_json(db, inv)
    inv.sender = _sender_json(row)
    inv.status = "storno" if inv.storno_of else "open"
    inv.issued_at = utcnow()
    inv.pdf = base64.b64encode(render_pdf(inv)).decode()
    audit.record(db, actor, "invoice", "ausgestellt", None, f"{inv.number} · {eur(inv.gross_cents)}",
                 family_id=inv.family_id, object_id=inv.id)
    db.commit()
    return inv


def pdf_bytes(inv: Invoice) -> bytes:
    return base64.b64decode(inv.pdf) if inv.pdf else b""


# ---------- Bezahlt, Storno, Versand ----------

def mark_paid(db: Session, inv: Invoice, paid_on: date, actor) -> int | None:
    """Bezahlt markieren und das Abo koppeln (aktiv, „bezahlt bis“ = Ende des Leistungszeitraums, falls später).
    Rückgabe: Familie, deren Abo sich geändert hat (für billing.apply), sonst None."""
    from . import billing
    _lock(db, inv)
    if inv.status != "open":
        raise InvoiceError("Nur offene Rechnungen lassen sich als bezahlt markieren.")
    inv.status, inv.paid_on = "paid", paid_on
    audit.record(db, actor, "invoice", "bezahlt", None, f"{inv.number} · {paid_on.isoformat()}",
                 family_id=inv.family_id, object_id=inv.id)
    family = db.get(Family, inv.family_id)
    changed = None
    if family is not None and family.wrapped_key is not None and inv.service_end:
        before = audit.snapshot(family, billing.AUDIT_FIELDS)
        end = datetime.combine(inv.service_end, time(23, 59))
        if family.plan == "free":
            family.plan = "standard"
        family.billing_status = "active"
        if family.paid_until is None or family.paid_until < end:
            family.paid_until = end
        audit.changes(db, actor, "subscription", before, family, family_id=family.id)
        changed = family.id
    db.commit()
    return changed


def storno(db: Session, inv: Invoice, actor) -> Invoice:
    """Storno-Rechnung zu einer offenen oder bezahlten Rechnung (negative Beträge, eigene Nummer). Das Abo bleibt
    unverändert – bei einer bezahlten Rechnung zeigt die Oberfläche „Abo prüfen“."""
    _lock(db, inv)
    if inv.status not in ("open", "paid"):
        raise InvoiceError("Stornieren lässt sich nur eine offene oder bezahlte Rechnung.")
    was_paid = inv.status == "paid"
    s = Invoice(family_id=inv.family_id, status="draft", storno_of=inv.id, service_start=inv.service_start,
                service_end=inv.service_end, reference=f"{inv.number} vom {inv.issue_date:%d.%m.%Y}")
    for line in inv.lines:
        s.lines.append(InvoiceLine(pos=line.pos, text=line.text, quantity=line.quantity, unit_cents=-line.unit_cents,
                                   total_cents=-line.total_cents))
    db.add(s)
    db.flush()
    inv.status = "canceled"
    issue(db, s, actor, address_json=inv.address)      # gleiche Rechnungsadresse wie das Original
    audit.record(db, actor, "invoice", "storniert", inv.number, f"{s.number}" + (" (war bezahlt: Abo prüfen)" if was_paid else ""),
                 family_id=inv.family_id, object_id=inv.id)
    db.commit()
    return s


def invoice_mail_ctx(inv: Invoice, address: dict, sender: dict) -> dict:
    """Variablen der Rechnungsmail (Zahlungsangaben nur bei offener, positiver Rechnung)."""
    return dict(name=address.get("name", ""), storno=inv.status == "storno", number=inv.number,
                issue_date=inv.issue_date, gross=eur(inv.gross_cents), due_date=inv.due_date,
                pay=inv.status != "storno" and inv.gross_cents > 0, holder=sender.get("bank_holder", ""),
                iban=sender.get("iban", ""), bic=sender.get("bic", ""), sender_name=sender.get("sender_name", ""))


def send(db: Session, inv: Invoice, actor, again: bool = False) -> bool:
    """Rechnung mit PDF an die Rechnungs-E-Mail der Familie (Zahlungsinformationen im Text)."""
    if inv.status not in ISSUED:
        raise InvoiceError("Nur ausgestellte Rechnungen lassen sich senden.")
    address = json.loads(inv.address or "{}")
    sender = json.loads(inv.sender or "{}")
    to = address.get("email", "")
    if not to:
        raise InvoiceError("Keine Rechnungs-E-Mail hinterlegt.")
    from . import mailer
    ok = mailer.send(to, "invoice", "de", attachments=[(f"{inv.number}.pdf", pdf_bytes(inv), "application/pdf")],
                     **invoice_mail_ctx(inv, address, sender))
    if ok:
        inv.sent_at = utcnow()
        audit.record(db, actor, "invoice", "erneut gesendet" if again else "gesendet", None, to,
                     family_id=inv.family_id, object_id=inv.id)
        db.commit()
    return ok


# ---------- Archiv, Aufbewahrung, Export ----------

def expires_on(inv: Invoice) -> date | None:
    """Letzter Aufbewahrungstag: Ende des Ausstellungsjahres + 8 Jahre."""
    return date(inv.year + RETENTION_YEARS, 12, 31) if inv.year else None


def purge_expired(db: Session, day: date | None = None) -> int:
    """Rechnungen nach Ablauf der Frist löschen (samt ihren Protokolleinträgen)."""
    day = day or today()
    ids = [i.id for i in db.scalars(select(Invoice).where(Invoice.year.is_not(None),
                                                         Invoice.year + RETENTION_YEARS < day.year))]
    if ids:
        db.execute(delete(AdminLog).where(AdminLog.object == "invoice", AdminLog.object_id.in_(ids)))
        db.execute(delete(Invoice).where(Invoice.id.in_(ids)))
        log.info("%d Rechnungen nach Ablauf der Aufbewahrungsfrist gelöscht", len(ids))
    db.commit()
    return len(ids)


def drop_drafts(db: Session, family_id: int) -> None:
    """Beim Löschen einer Familie: nur Entwürfe verschwinden, ausgestellte Rechnungen bleiben im Archiv."""
    db.execute(delete(Invoice).where(Invoice.family_id == family_id, Invoice.status == "draft"))


def export_csv(db: Session, year: int) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Nummer", "Datum", "Familie", "Leistung von", "Leistung bis", "Netto", "USt", "Brutto", "Status",
                "Bezahlt am", "Storno zu"])
    numbers = dict(db.execute(select(Invoice.id, Invoice.number)).all())
    for inv in db.scalars(select(Invoice).where(Invoice.year == year).order_by(Invoice.seq)):
        w.writerow([inv.number, inv.issue_date.isoformat(), f"#{inv.family_id} {inv.family_label}".strip(),
                    inv.service_start.isoformat() if inv.service_start else "",
                    inv.service_end.isoformat() if inv.service_end else "",
                    f"{inv.net_cents / 100:.2f}".replace(".", ","), f"{inv.tax_cents / 100:.2f}".replace(".", ","),
                    f"{inv.gross_cents / 100:.2f}".replace(".", ","), STATUSES[inv.status],
                    inv.paid_on.isoformat() if inv.paid_on else "", numbers.get(inv.storno_of, "") or ""])
    return buf.getvalue()


def export_zip(db: Session, year: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"rechnungen-{year}.csv", export_csv(db, year))
        for inv in db.scalars(select(Invoice).where(Invoice.year == year).order_by(Invoice.seq)):
            zf.writestr(f"{inv.number}.pdf", pdf_bytes(inv))
    return buf.getvalue()


# ---------- PDF ----------

def render_pdf(inv: Invoice) -> bytes:
    """Schlichtes A4-Layout nahe DIN 5008 (Anschriftfeld links oben, Absender rechts, Positionen, Summen,
    Zahlungsinformationen, Fußzeile). Deutsch."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    a = json.loads(inv.address or "{}")
    s = json.loads(inv.sender or "{}")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    title = "Stornorechnung" if inv.storno_of else "Rechnung"
    c.setTitle(f"{title} {inv.number}")
    c.setAuthor(s.get("sender_name", ""))
    width, height = A4

    def text(x, y, value, size=10, bold=False, right=False):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        (c.drawRightString if right else c.drawString)(x, y, value)

    # Absenderzeile über dem Anschriftfeld, Anschrift (DIN 5008 Form B: 45 mm von oben, 20 mm von links)
    sender_line = f"{s.get('sender_name', '')} · {s.get('sender_street', '')} · {s.get('sender_postal_code', '')} {s.get('sender_city', '')}"
    text(20 * mm, height - 50 * mm, sender_line[:110], 7)
    y = height - 57 * mm
    for line in (a.get("name", ""), a.get("street", ""), f"{a.get('postal_code', '')} {a.get('city', '')}",
                 a.get("country", "") if a.get("country", "").strip().lower() not in ("deutschland", "germany", "de") else ""):
        if line.strip():
            text(20 * mm, y, line, 10)
            y -= 5 * mm
    # Absender rechts oben
    y = height - 25 * mm
    for i, line in enumerate((s.get("sender_name", ""), s.get("sender_street", ""),
                              f"{s.get('sender_postal_code', '')} {s.get('sender_city', '')}", s.get("contact", ""))):
        text(width - 20 * mm, y, line, 9, bold=i == 0, right=True)
        y -= 4.5 * mm
    # Rechnungsdaten
    y = height - 100 * mm
    text(20 * mm, y, f"{title} {inv.number}", 14, bold=True)
    y -= 8 * mm
    facts = [("Rechnungsdatum", inv.issue_date.strftime("%d.%m.%Y")),
             ("Leistungszeitraum", f"{inv.service_start:%d.%m.%Y} – {inv.service_end:%d.%m.%Y}")]
    if inv.storno_of is None:
        facts.append(("Fällig bis", inv.due_date.strftime("%d.%m.%Y")))
    if inv.reference:
        facts.append(("Storno zu Rechnung", inv.reference))
    if a.get("vat_id"):
        facts.append(("USt-IdNr. Empfänger", a["vat_id"]))
    for label, value in facts:
        text(20 * mm, y, label, 9)
        text(65 * mm, y, value, 9)
        y -= 5 * mm
    # Positionen
    y -= 4 * mm
    cols = (20 * mm, 30 * mm, 135 * mm, 165 * mm, width - 20 * mm)
    for x, head, right in ((cols[0], "Pos.", False), (cols[1], "Leistung", False), (cols[2], "Menge", True),
                           (cols[3], "Einzelpreis", True), (cols[4], "Betrag", True)):
        text(x, y, head, 9, bold=True, right=right)
    y -= 2 * mm
    c.line(20 * mm, y, width - 20 * mm, y)
    y -= 5 * mm
    for line in inv.lines:
        text(cols[0], y, str(line.pos), 9)
        text(cols[1], y, line.text[:70], 9)
        text(cols[2], y, str(line.quantity), 9, right=True)
        text(cols[3], y, eur(line.unit_cents), 9, right=True)
        text(cols[4], y, eur(line.total_cents), 9, right=True)
        y -= 5 * mm
        if y < 60 * mm:
            c.showPage()
            y = height - 30 * mm
    c.line(110 * mm, y + 2 * mm, width - 20 * mm, y + 2 * mm)
    y -= 3 * mm
    if inv.tax_mode == "standard":
        for label, value, bold in (("Summe netto", inv.net_cents, False),
                                   (f"Umsatzsteuer {inv.tax_rate} %", inv.tax_cents, False),
                                   ("Rechnungsbetrag", inv.gross_cents, True)):
            text(cols[3], y, label, 9, bold=bold, right=True)
            text(cols[4], y, eur(value), 9, bold=bold, right=True)
            y -= 5 * mm
    else:
        text(cols[3], y, "Rechnungsbetrag", 9, bold=True, right=True)
        text(cols[4], y, eur(inv.gross_cents), 9, bold=True, right=True)
        y -= 7 * mm
        text(20 * mm, y, SMALL_BUSINESS_NOTE, 9)
        y -= 5 * mm
    y -= 6 * mm
    if inv.storno_of is None and inv.gross_cents > 0:
        for line in (f"Bitte überweise den Rechnungsbetrag bis {inv.due_date:%d.%m.%Y} unter Angabe der "
                     f"Rechnungsnummer {inv.number}.",
                     f"Kontoinhaber: {s.get('bank_holder', '')} · IBAN: {s.get('iban', '')}"
                     + (f" · BIC: {s['bic']}" if s.get("bic") else "")):
            text(20 * mm, y, line, 9)
            y -= 5 * mm
    elif inv.storno_of is not None:
        text(20 * mm, y, f"Diese Stornorechnung hebt die Rechnung {inv.reference} vollständig auf.", 9)
        y -= 5 * mm
    for line in (s.get("footer", "") or "").splitlines()[:4]:
        text(20 * mm, y, line[:110], 9)
        y -= 5 * mm
    # Fußzeile
    c.line(20 * mm, 22 * mm, width - 20 * mm, 22 * mm)
    foot = [f"{s.get('sender_name', '')} · {s.get('sender_street', '')} · {s.get('sender_postal_code', '')} {s.get('sender_city', '')}",
            f"Steuernummer/USt-IdNr.: {s.get('tax_id', '')} · {s.get('contact', '')}",
            f"Bank: {s.get('bank_holder', '')} · IBAN {s.get('iban', '')}" + (f" · BIC {s['bic']}" if s.get("bic") else "")]
    y = 17 * mm
    for line in foot:
        c.setFont("Helvetica", 7)
        c.drawCentredString(width / 2, y, line[:130])
        y -= 3.5 * mm
    c.showPage()
    c.save()
    return buf.getvalue()
