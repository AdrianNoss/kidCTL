"""Datenmodell. Alle Zeitstempel als naive UTC-Datetimes."""

from __future__ import annotations

import json

from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, Time, UniqueConstraint, text,
)
from sqlalchemy import event
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from .crypto import Matchable, PlatformSealed, Sealed
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _server_version() -> str:
    from .release_notes import RELEASES
    return RELEASES[0]["version"]


class Family(Base):
    """Mandant: eine Familie (Haushalt). Im Einzelbetrieb gibt es genau eine."""
    __tablename__ = "family"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Name der Familie (von ihr selbst oder vom Betreiber gesetzt) – Plattform-Schlüssel, im Verwaltungsbereich lesbar
    name: Mapped[str] = mapped_column(PlatformSealed("family.name"), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Datenschlüssel der Familie, mit dem Hauptschlüssel eingepackt (app.crypto); NULL = gelöscht/unlesbar
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Ruhemodus seit (Abo/Testphase abgelaufen, Etappe 5 setzt ihn): Agenten heben alle Einschränkungen auf
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Abo (Etappe 5, nur SaaS – app.billing): Tarif free | standard, Status trial | active | canceled | past_due
    plan: Mapped[str] = mapped_column(String(16), default="free", server_default="free")
    billing_status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # leer bei „aktiv“ = ohne Ende
    billing_provider: Mapped[str] = mapped_column(String(32), default="", server_default="")   # neutral
    billing_customer: Mapped[str] = mapped_column(String(128), default="", server_default="")  # Kundennummer dort
    billing_notified: Mapped[str] = mapped_column(String(255), default="", server_default="")  # gesendete Hinweise
    # Newsletter (Opt-in): Zeitpunkt der Zustimmung; NULL = keine Neuigkeiten per Mail
    newsletter_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FamilyScoped:
    """Mixin für alle Tabellen, die einer Familie gehören. Abfragen werden über den Familienkontext der Sitzung
    automatisch gefiltert, neue Zeilen bekommen die Familie aus dem Kontext (siehe app.tenant)."""

    @declared_attr
    def family_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("family.id", ondelete="CASCADE"), index=True)


ROLE_OWNER, ROLE_PARENT = "owner", "parent"


class Membership(FamilyScoped, Base):
    """Account <-> Familie mit Rolle. Vorerst genau eine Mitgliedschaft je Account (Unique auf parent_id)."""
    __tablename__ = "membership"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id", ondelete="CASCADE"), unique=True)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_PARENT)   # owner | parent
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FamilyInvite(FamilyScoped, Base):
    """Einladung eines weiteren Elternteils (Inhaber). Gespeichert wird nur der Hash des Links; die E-Mail verschlüsselt."""
    __tablename__ = "family_invite"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(Sealed("family_invite.email"))
    display_name: Mapped[str] = mapped_column(Sealed("family_invite.display_name"), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Parent(Base):
    """Eltern-Account; gehört über ``Membership`` zu genau einer Familie."""
    __tablename__ = "parent"
    # E-Mail verschlüsselt (Schlüssel der Familie); Anmeldung und Eindeutigkeit über den Suchwert email_index
    __table_args__ = (Index("ix_parent_email_index", "email_index", unique=True,
                            postgresql_where=text("email_index <> ''")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str] = mapped_column(String(64))
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # Replay-Schutz
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    email: Mapped[str] = mapped_column(Sealed("parent.email"), default="")  # Anmeldung, „Passwort vergessen“; SaaS: Pflicht
    email_index: Mapped[str] = mapped_column(String(64), default="")   # HMAC(lower(email)), app.crypto.email_index
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # Bestätigungslink
    report_interval: Mapped[str] = mapped_column(String(16), default="")  # Nutzungsbericht: "" | weekly | monthly
    report_sent: Mapped[str] = mapped_column(String(16), default="")      # zuletzt gesendeter Zeitraum (Schlüssel)
    language: Mapped[str] = mapped_column(String(8), default="")           # "" = automatisch (Browser) | de | en
    detected_language: Mapped[str] = mapped_column(String(8), default="")  # zuletzt erkannt (für Mails/Push)
    # Hinweis „Neu in Version …“: zuletzt bestätigte Server-Version; neue Konten starten bei der laufenden
    seen_version: Mapped[str] = mapped_column(String(32), default=lambda: _server_version())


class ParentToken(Base):
    """Einmal-Link: Passwort zurücksetzen (E-Mail) bzw. Authenticator einrichten/wechseln.
    Gespeichert wird nur der Hash; ``secret`` = neues TOTP-Secret bis zur Bestätigung."""
    __tablename__ = "parent_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(16))   # password_reset | totp_setup | totp_change
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    secret: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ParentSession(Base):
    __tablename__ = "parent_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # frische Bestätigung

    parent: Mapped[Parent] = relationship()


class Operator(Base):
    """Betreiber-Account für den Verwaltungsbereich (``/admin``) – getrennt von Eltern, ohne Familie."""
    __tablename__ = "operator"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str] = mapped_column(String(64))
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    role: Mapped[str] = mapped_column(String(16), default="admin", server_default="admin")   # admin | support
    email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    ticket_mails: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class AdminLog(Base):
    """Verwaltungsprotokoll: jede Änderung durch Betreiber, Webhook oder Hintergrundjob (für Familien unsichtbar).
    Bleibt beim Löschen einer Familie erhalten (``family_id`` ohne Fremdschlüssel); Werte mit dem Plattform-Schlüssel."""
    __tablename__ = "admin_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))                  # Betreiber-Name | Webhook | System
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operator.id", ondelete="SET NULL"), nullable=True)
    family_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    object: Mapped[str] = mapped_column(String(32))                 # family | subscription | code | ticket | invoice …
    object_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field: Mapped[str] = mapped_column(String(64), default="")
    old: Mapped[str] = mapped_column(PlatformSealed("admin_log.old"), default="")
    new: Mapped[str] = mapped_column(PlatformSealed("admin_log.new"), default="")


class OperatorSession(Base):
    __tablename__ = "operator_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operator.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # frische Bestätigung

    operator: Mapped[Operator] = relationship()


class SignupCode(Base):
    """Einladungscode für die Registrierung (``REGISTRATION=invite``): einmalig, nur der Hash wird gespeichert."""
    __tablename__ = "signup_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    note: Mapped[str] = mapped_column(String(255), default="")      # Notiz des Betreibers (für wen)
    plan: Mapped[str] = mapped_column(String(16), default="", server_default="")   # "" = Testphase | free
    created_by: Mapped[int | None] = mapped_column(ForeignKey("operator.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    family_id: Mapped[int | None] = mapped_column(ForeignKey("family.id", ondelete="SET NULL"), nullable=True)
    # Einladung per Mail: Adresse (Plattform-Schlüssel) für „Erneut senden“, gelöscht nach Einlösen/Ablauf
    email: Mapped[str | None] = mapped_column(PlatformSealed("signup_code.email"), nullable=True)
    lang: Mapped[str] = mapped_column(String(2), default="de", server_default="de")


class SupportGrant(FamilyScoped, Base):
    """Freigabe der Familie für die Support-Ansicht (nur lesend), zeitlich begrenzt, jederzeit widerrufbar."""
    __tablename__ = "support_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SupportAccess(FamilyScoped, Base):
    """Protokoll der Support-Zugriffe – für die Familie sichtbar, 2 Jahre aufbewahrt."""
    __tablename__ = "support_access"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    operator: Mapped[str] = mapped_column(String(64), default="")   # Benutzername des Betreibers
    path: Mapped[str] = mapped_column(String(255), default="")      # aufgerufene Seite


class SupportSession(Base):
    """Laufende Support-Ansicht eines Betreibers in einer Familie (Cookie ``kidctl_support``). Gilt nur, solange
    Freigabe und Betreiber-Sitzung gelten."""
    __tablename__ = "support_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    operator_session_id: Mapped[int] = mapped_column(ForeignKey("operator_session.id", ondelete="CASCADE"))
    grant_id: Mapped[int] = mapped_column(ForeignKey("support_grant.id", ondelete="CASCADE"))
    family_id: Mapped[int] = mapped_column(ForeignKey("family.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class BillingAddress(FamilyScoped, Base):
    """Rechnungsangaben, von der Familie selbst hinterlegt. Plattform-Schlüssel: Der Betreiber braucht sie für
    Rechnungen; mit der Familie gelöscht (CASCADE)."""
    __tablename__ = "billing_address"
    __table_args__ = (UniqueConstraint("family_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(PlatformSealed("billing_address.name"), default="")        # Name bzw. Firma
    street: Mapped[str] = mapped_column(PlatformSealed("billing_address.street"), default="")
    postal_code: Mapped[str] = mapped_column(PlatformSealed("billing_address.postal_code"), default="")
    city: Mapped[str] = mapped_column(PlatformSealed("billing_address.city"), default="")
    country: Mapped[str] = mapped_column(PlatformSealed("billing_address.country"), default="")
    email: Mapped[str] = mapped_column(PlatformSealed("billing_address.email"), default="")      # für Rechnungen
    vat_id: Mapped[str] = mapped_column(PlatformSealed("billing_address.vat_id"), default="")    # optional
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    REQUIRED = ("name", "street", "postal_code", "city", "country", "email")

    @property
    def complete(self) -> bool:
        return all(getattr(self, f).strip() for f in self.REQUIRED)


class Ticket(FamilyScoped, Base):
    """Support-Ticket einer Familie (nur SaaS). Betreff und Metadaten mit dem Plattform-Schlüssel – der Support muss sie
    lesen; mit der Familie gelöscht (CASCADE), nicht im Familien-Export."""
    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(PlatformSealed("ticket.subject"))
    category: Mapped[str] = mapped_column(String(16))               # tech | billing | other
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)   # new | in_progress | waiting | closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)        # letzte Nachricht/Statusänderung
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    meta: Mapped[str] = mapped_column(PlatformSealed("ticket.meta"), default="")  # JSON: Agenten, Plattformen, Abo, Sprache
    last_operator: Mapped[str] = mapped_column(String(64), default="")           # „zuletzt bearbeitet von“

    messages: Mapped[list[TicketMessage]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketMessage.id")


class TicketMessage(FamilyScoped, Base):
    __tablename__ = "ticket_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    from_support: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    author: Mapped[str] = mapped_column(PlatformSealed("ticket_message.author"), default="")  # Anzeigename bzw. Betreiber
    internal: Mapped[bool] = mapped_column(Boolean, default=False)   # interne Notiz: nur für den Support
    body: Mapped[str] = mapped_column(PlatformSealed("ticket_message.body"))

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class Newsletter(Base):
    """Neuigkeiten an alle Familien mit Zustimmung (Verwaltungsbereich). Betreff/Text deutsch, optional englisch;
    jedes Elternteil bekommt seine Sprache (ohne Englisch: Deutsch). Versendete bleiben als Archiv erhalten."""
    __tablename__ = "newsletter"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")          # draft | sending | sent
    subject_de: Mapped[str] = mapped_column(String(200), default="")
    body_de: Mapped[str] = mapped_column(Text, default="")
    subject_en: Mapped[str] = mapped_column(String(200), default="")
    body_en: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_by: Mapped[str] = mapped_column(String(64), default="")
    families: Mapped[int] = mapped_column(Integer, default=0)                 # Familien mit Zustimmung beim Versand
    recipients: Mapped[int] = mapped_column(Integer, default=0)               # zugestellte Mails


class Branding(Base):
    """Whitelabel (eine Zeile, id=1): eigener Name, Logo und App-Icon für Eltern-WebUI, PWA und Mails. Im
    Verwaltungsbereich gepflegt und protokolliert (Ausnahme von „Konfiguration nur über .env“). Bilder als PNG,
    beim Hochladen normalisiert; „powered by KidCTL“ bleibt immer sichtbar."""
    __tablename__ = "branding"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    logo_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)       # PNG, höchstens 512×128
    icon: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)       # PNG 512×512 (Favicon, App-Icon)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # Anbieter (Fußzeile der Mails; leere Felder entfallen)
    provider_name: Mapped[str] = mapped_column(String(128), default="", server_default="")
    provider_address: Mapped[str] = mapped_column(String(255), default="", server_default="")
    provider_email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    imprint_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    privacy_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    news_url: Mapped[str] = mapped_column(String(255), default="", server_default="")   # leer: Projekt-News
    # Link unter den Anmeldeseiten (nur https); leer: Projektseite
    homepage_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    homepage_label_de: Mapped[str] = mapped_column(String(64), default="", server_default="")
    homepage_label_en: Mapped[str] = mapped_column(String(64), default="", server_default="")


class InvoiceSettings(Base):
    """Absender, Bankverbindung, Fußzeile der Rechnungen (eine Zeile, id=1). Ausnahme von „Konfiguration nur über
    .env“: im Verwaltungsbereich gepflegt und protokolliert."""
    __tablename__ = "invoice_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_name: Mapped[str] = mapped_column(String(128), default="")
    sender_street: Mapped[str] = mapped_column(String(128), default="")
    sender_postal_code: Mapped[str] = mapped_column(String(16), default="")
    sender_city: Mapped[str] = mapped_column(String(64), default="")
    sender_country: Mapped[str] = mapped_column(String(64), default="Deutschland")
    tax_id: Mapped[str] = mapped_column(String(64), default="")      # Steuernummer oder USt-IdNr.
    contact: Mapped[str] = mapped_column(String(255), default="")    # E-Mail/Telefon/Web
    bank_holder: Mapped[str] = mapped_column(String(128), default="")
    iban: Mapped[str] = mapped_column(String(64), default="")
    bic: Mapped[str] = mapped_column(String(16), default="")
    footer: Mapped[str] = mapped_column(Text, default="")             # Zahlungshinweis/Fußzeile

    REQUIRED = ("sender_name", "sender_street", "sender_postal_code", "sender_city", "sender_country", "tax_id",
                "contact", "bank_holder", "iban")


class PriceItem(Base):
    """Preisliste (Vorlagen für Rechnungspositionen); Betrag in Cent."""
    __tablename__ = "price_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    amount_cents: Mapped[int] = mapped_column(Integer)
    months: Mapped[int] = mapped_column(Integer, default=12)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class InvoiceCounter(Base):
    """Nummernkreis je Jahr: lückenlos, Vergabe unter Zeilensperre in der Transaktion des Ausstellens."""
    __tablename__ = "invoice_counter"

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last: Mapped[int] = mapped_column(Integer, default=0)


class Invoice(Base):
    """Rechnung. Hängt nicht am Familienschlüssel und überlebt das Löschen der Familie (``family_id`` ohne
    Fremdschlüssel). Nach dem Ausstellen unveränderlich – Korrektur nur per Storno-Rechnung."""
    __tablename__ = "invoice"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)   # draft | open | paid | canceled | storno
    number: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    tax_mode: Mapped[str] = mapped_column(String(16), default="")          # beim Ausstellen eingefroren
    tax_rate: Mapped[int] = mapped_column(Integer, default=0)              # Prozent
    net_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    gross_cents: Mapped[int] = mapped_column(Integer, default=0)
    family_label: Mapped[str] = mapped_column(PlatformSealed("invoice.family_label"), default="")
    address: Mapped[str] = mapped_column(PlatformSealed("invoice.address"), default="")   # JSON-Kopie beim Ausstellen
    sender: Mapped[str] = mapped_column(PlatformSealed("invoice.sender"), default="")     # JSON-Kopie beim Ausstellen
    pdf: Mapped[str] = mapped_column(PlatformSealed("invoice.pdf"), default="")           # Base64
    storno_of: Mapped[int | None] = mapped_column(ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True)
    reference: Mapped[str] = mapped_column(String(64), default="")   # Storno: Nummer und Datum des Originals
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                    order_by="InvoiceLine.pos")


class InvoiceLine(Base):
    __tablename__ = "invoice_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id", ondelete="CASCADE"), index=True)
    pos: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_cents: Mapped[int] = mapped_column(Integer)                 # negativ = Rabatt
    total_cents: Mapped[int] = mapped_column(Integer)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class LoginAttempt(Base):
    """Fehlgeschlagene Logins/Kopplungen für Rate-Limiting."""
    __tablename__ = "login_attempt"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="login")  # login | totp | reset | enroll
    ip: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LoginTicket(Base):
    """Zwischenschritt der Anmeldung: Passwort war richtig, der Zweitfaktor fehlt noch. Nur der Hash des Tokens
    (Cookie) wird gespeichert; 5 Minuten gültig, einmal verwendbar, höchstens 5 Code-Versuche."""
    __tablename__ = "login_ticket"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))              # parent | operator
    account_id: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Passkey(Base):
    """Passkey (WebAuthn, auffindbarer Schlüssel) eines Elternteils oder Betreibers – vollständiger Login ohne
    Passwort und Code. Plattform-Bereich, damit die Anmeldung vor der Familienzuordnung funktioniert; der Name ist
    mit dem Plattform-Schlüssel verschlüsselt."""
    __tablename__ = "passkey"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))                    # parent | operator
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    credential_id: Mapped[str] = mapped_column(String(512), unique=True)   # base64url
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[str] = mapped_column(String(128), default="")
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)     # synchronisiert (z. B. iCloud, Google)
    name: Mapped[str] = mapped_column(PlatformSealed("passkey.name"), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebAuthnChallenge(Base):
    """Challenge für Registrierung, Anmeldung oder Bestätigung per Passkey: serverseitig, einmalig, 5 Minuten.
    Das Cookie trägt nur ein Token, gespeichert ist dessen Hash."""
    __tablename__ = "webauthn_challenge"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16))                 # register | login | confirm
    kind: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    challenge: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class PushSubscription(Base):
    __tablename__ = "push_subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Child(FamilyScoped, Base):
    """Kind – zentrale Einheit, trägt alle Policies."""
    __tablename__ = "child"
    __table_args__ = (UniqueConstraint("family_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Matchable("child.name"))
    weekdays_same: Mapped[bool] = mapped_column(Boolean, default=True)  # UI: Werktage/Wochenende gleich
    # Veraltet (nie benutzt, Standard war „whitelist“) – ersetzt durch web_mode
    web_filter_mode: Mapped[str] = mapped_column(String(16), default="whitelist")
    web_mode: Mapped[str] = mapped_column(String(16), default="off")  # off | whitelist | blacklist
    safesearch: Mapped[bool] = mapped_column(Boolean, default=True)
    program_mode: Mapped[str] = mapped_column(String(16), default="off")  # off | audit | enforce
    # „Jetzt sperren“: gesperrt bis locked_until (UTC) bzw. bis zur Freigabe (None)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lock_grace_minutes: Mapped[int] = mapped_column(Integer, default=1)  # Vorwarnung bis zur Abmeldung
    # Zeitkonto (siehe app.timebank): Anteil nicht genutzter Minuten, Deckel je Tag, Verfall in Tagen.
    # bank_since = erster Tag, dessen Restminuten eingezahlt werden (Tag nach dem Einschalten)
    bank_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    bank_percent: Mapped[int] = mapped_column(Integer, default=50)
    bank_cap_minutes: Mapped[int] = mapped_column(Integer, default=30)
    bank_expiry_days: Mapped[int] = mapped_column(Integer, default=7)
    bank_since: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    rules: Mapped[list[WeekdayRule]] = relationship(
        back_populates="child", cascade="all, delete-orphan", order_by="WeekdayRule.weekday")
    # Ferienvorlage (leer = in den Ferien gilt die Wochenvorlage)
    vacation_rules: Mapped[list[VacationRule]] = relationship(
        back_populates="child", cascade="all, delete-orphan", order_by="VacationRule.weekday")


class _BlocksMixin:
    """Sperrzeiten einer Tagesregel als Liste von ``protocol.Block`` (Spalte ``blocks``)."""
    blocks: str | None

    @property
    def block_list(self) -> list:
        from kidctl_common.protocol import Block
        return [Block.model_validate(b) for b in json.loads(self.blocks)] if self.blocks else []

    @block_list.setter
    def block_list(self, value: list) -> None:
        self.blocks = json.dumps([b.model_dump(mode="json") for b in value]) if value else None


class WeekdayRule(_BlocksMixin, FamilyScoped, Base):
    """Wochenvorlage: je Wochentag Kontingent + erlaubtes Zeitfenster (Europe/Berlin)."""
    __tablename__ = "weekday_rule"
    __table_args__ = (UniqueConstraint("child_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Montag … 6 = Sonntag
    quota_minutes: Mapped[int] = mapped_column(Integer, default=60)
    window_start: Mapped[time] = mapped_column(Time, default=time(8, 0))
    window_end: Mapped[time] = mapped_column(Time, default=time(20, 0))
    game_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Spiele-Limit; None = keins
    # Sperrzeiten (bis zu 3, JSON-Liste von {start, end, name}; verschlüsselt, None = keine). Fensterende < Beginn
    # bedeutet „über Mitternacht“ (siehe spec.md „Sperrzeiten und Fenster über Mitternacht“).
    blocks: Mapped[str | None] = mapped_column(Sealed("weekday_rule.blocks"), nullable=True)

    child: Mapped[Child] = relationship(back_populates="rules")


class VacationRule(_BlocksMixin, FamilyScoped, Base):
    """Ferienvorlage: wie ``WeekdayRule``, gilt an Tagen in einem Ferienzeitraum der Familie."""
    __tablename__ = "vacation_rule"
    __table_args__ = (UniqueConstraint("child_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Montag … 6 = Sonntag
    quota_minutes: Mapped[int] = mapped_column(Integer, default=60)
    window_start: Mapped[time] = mapped_column(Time, default=time(8, 0))
    window_end: Mapped[time] = mapped_column(Time, default=time(20, 0))
    game_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocks: Mapped[str | None] = mapped_column(Sealed("vacation_rule.blocks"), nullable=True)  # wie WeekdayRule

    child: Mapped[Child] = relationship(back_populates="vacation_rules")


class Vacation(FamilyScoped, Base):
    """Ferienzeitraum der Familie (erster bis letzter Tag einschließlich)."""
    __tablename__ = "vacation"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Sealed("vacation.name"), default="")
    first_day: Mapped[date] = mapped_column(Date)
    last_day: Mapped[date] = mapped_column(Date)


class HolidayRegion(FamilyScoped, Base):
    """Gesetzliche Feiertage der Familie: Land (DE/AT/CH), Bundesland/Kanton (leer = nur landesweit) und der
    Wochentag, wie der ein Feiertag gilt. Höchstens eine Zeile je Familie; keine Zeile = keine Feiertage."""
    __tablename__ = "holiday_region"
    __table_args__ = (UniqueConstraint("family_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(2))
    subdiv: Mapped[str] = mapped_column(String(16), default="")
    as_weekday: Mapped[int] = mapped_column(Integer, default=6)


class DayException(FamilyScoped, Base):
    """Tagesausnahme (Verlängerung/Gutschrift), gilt nur für ``day``."""
    __tablename__ = "day_exception"
    __table_args__ = (UniqueConstraint("child_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    extra_minutes: Mapped[int] = mapped_column(Integer, default=0)
    window_end_override: Mapped[time | None] = mapped_column(Time, nullable=True)
    extra_game_minutes: Mapped[int] = mapped_column(Integer, default=0)  # Spielzeit-Gutschrift


class Device(FamilyScoped, Base):
    __tablename__ = "device"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Sealed("device.name"))
    hostname: Mapped[str] = mapped_column(Sealed("device.hostname"), default="")
    platform: Mapped[str] = mapped_column(String(16), default="linux")   # linux | windows (aus hello)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agent_version: Mapped[str] = mapped_column(String(64), default="")
    protocol_version: Mapped[int] = mapped_column(Integer, default=0)
    protocol_unsupported: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_after_hours: Mapped[int] = mapped_column(Integer, default=48)
    policy_version: Mapped[str] = mapped_column(String(64), default="")  # vom Agenten bestätigt
    offline_alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    integrity_json: Mapped[str] = mapped_column(Sealed("device.integrity_json"), default="")  # letzte IntegrityReport.checks
    integrity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    update_alerted: Mapped[str] = mapped_column(String(32), default="")  # Version, zu der schon gewarnt wurde
    features: Mapped[str] = mapped_column(String(255), default="", server_default="")   # aus hello, kommagetrennt
    # Update auf Knopfdruck: Zeitpunkt, Ausgangsversion, Stand (running|failed|current|done) und Meldung des Agenten
    update_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    update_from: Mapped[str] = mapped_column(String(64), default="", server_default="")
    update_state: Mapped[str] = mapped_column(String(16), default="", server_default="")
    update_detail: Mapped[str | None] = mapped_column(Sealed("device.update_detail"), nullable=True)

    @property
    def integrity_checks(self) -> list[dict]:
        return json.loads(self.integrity_json) if self.integrity_json else []

    @property
    def integrity_failed(self) -> list[dict]:
        return [c for c in self.integrity_checks if not c.get("ok")]

    users: Mapped[list[DeviceUser]] = relationship(
        back_populates="device", cascade="all, delete-orphan", order_by="DeviceUser.id")


class DeviceUser(FamilyScoped, Base):
    """Zuordnung (Gerät, Linux-User) -> Kind. ``child_id`` NULL = nicht eingeschränkt."""
    __tablename__ = "device_user"
    __table_args__ = (UniqueConstraint("device_id", "username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(Matchable("device_user.username"))
    uid: Mapped[int] = mapped_column(Integer)
    full_name: Mapped[str] = mapped_column(Sealed("device_user.full_name"), default="")
    present: Mapped[bool] = mapped_column(Boolean, default=True)  # zuletzt vom Agenten gemeldet
    child_id: Mapped[int | None] = mapped_column(ForeignKey("child.id", ondelete="SET NULL"), nullable=True)

    device: Mapped[Device] = relationship(back_populates="users")
    child: Mapped[Child | None] = relationship()


class UsageRecord(FamilyScoped, Base):
    """Absolute Nutzungszeit je (Gerät, Linux-User, Tag), wie vom Agenten gemeldet."""
    __tablename__ = "usage_record"
    __table_args__ = (UniqueConstraint("device_id", "username", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL = Gerät entfernt; Statistik je Kind bleibt erhalten
    device_id: Mapped[int | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(Matchable("usage_record.username"))
    day: Mapped[date] = mapped_column(Date, index=True)
    child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    game_seconds: Mapped[int] = mapped_column(Integer, default=0)  # davon mit laufendem Spiel
    # Zeit je Limit (Programm/Kategorie) als JSON {Schlüssel: Sekunden}, verschlüsselt (nennt Programme)
    limit_seconds: Mapped[str | None] = mapped_column(Sealed("usage_record.limit_seconds"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeviceApp(FamilyScoped, Base):
    """Vom Agenten gemeldete installierte Anwendung (systemweiter .desktop-Eintrag)."""
    __tablename__ = "device_app"
    __table_args__ = (UniqueConstraint("device_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(512))     # exe:<pfad> | flatpak:<app-id>
    name: Mapped[str] = mapped_column(String(255))
    icon: Mapped[str] = mapped_column(String(255), default="")
    desktop_id: Mapped[str] = mapped_column(String(255), default="")
    categories: Mapped[str] = mapped_column(Text, default="")  # freedesktop-Kategorien, „;“-getrennt
    name_en: Mapped[str] = mapped_column(String(255), default="")  # englischer Name (Name= ohne Sprachzusatz)

    @property
    def label(self) -> str:
        """Name in der Sprache der laufenden Anfrage."""
        from kidctl_common.i18n import current
        return self.name_en if current() == "en" and self.name_en else self.name


class ChildGameApp(FamilyScoped, Base):
    """App, die für ein Kind als Spiel zählt (Spiele-Limit, Spielzeit), zusätzlich zur Kategorie „Spiele“
    (ergänzt 2026-11-06: Windows-Verknüpfungen haben keine Kategorien, z. B. Roblox, Minecraft-Launcher)."""
    __tablename__ = "child_game_app"
    __table_args__ = (UniqueConstraint("child_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(512))


class ChildAppRule(FamilyScoped, Base):
    """Einstellung je (Kind, App-Schlüssel) aus dem Programmkatalog (ergänzt 2026-11-25). ``free`` = „zählt nicht“:
    läuft nur diese App, verbraucht sie keine Bildschirmzeit (Browser usw. nie, siehe ``kidctl_common.freeapps``).
    ``limit_minutes`` = eigenes Tageslimit (ergänzt 2026-11-25); ``key`` dann auch eine Kategorie ``cat:…``
    (``kidctl_common.applimits``). Zeilen ohne jede Einstellung werden gelöscht."""
    __tablename__ = "child_app_rule"
    __table_args__ = (UniqueConstraint("child_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(512))
    free: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ChildProgram(FamilyScoped, Base):
    """Von den Eltern freigegebenes Programm (App-Schlüssel) für ein Kind."""
    __tablename__ = "child_program"
    __table_args__ = (UniqueConstraint("child_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(512))


class ChildCustomProgram(FamilyScoped, Base):
    """Von den Eltern selbst eingetragenes Programm (nicht im Katalog der Geräte, z. B. /usr/bin/git)."""
    __tablename__ = "child_custom_program"
    __table_args__ = (UniqueConstraint("child_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(512))   # exe:<pfad>
    name: Mapped[str] = mapped_column(String(64))


class BlockedEvent(FamilyScoped, Base):
    """Protokoll gesperrter Aktionen (kein Verlauf erlaubter Aktionen)."""
    __tablename__ = "blocked_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(Matchable("blocked_event.username"))
    at: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="program")
    key: Mapped[str] = mapped_column(Matchable("blocked_event.key"))
    name: Mapped[str] = mapped_column(Sealed("blocked_event.name"))
    detail: Mapped[str] = mapped_column(Sealed("blocked_event.detail"), default="")
    audit: Mapped[bool] = mapped_column(Boolean, default=False)
    detail_code: Mapped[str] = mapped_column(String(32), default="")   # sprachneutral (Agent ab 0.26)
    detail_arg: Mapped[str] = mapped_column(Sealed("blocked_event.detail_arg"), default="")


class GameSession(FamilyScoped, Base):
    """Spielsitzung (aus den „spielt gerade“-Meldungen des Agenten, etwa minütlich)."""
    __tablename__ = "game_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(Matchable("game_session.username"))
    key: Mapped[str] = mapped_column(Matchable("game_session.key"))      # game:steam:<id> / game:heroic:<quelle>:<id>
    name: Mapped[str] = mapped_column(Sealed("game_session.name"))
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)   # UTC
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ChildTempGrant(FamilyScoped, Base):
    """Freigabe „nur heute“ (Programm oder Website) aus einer Freigabe-Anfrage."""
    __tablename__ = "child_temp_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(512))   # exe:… | flatpak:… | domain:…
    day: Mapped[date] = mapped_column(Date)          # lokaler Kalendertag
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChildWebBundle(FamilyScoped, Base):
    """Gewähltes Dienst-Bundle (Whitelist-Modus)."""
    __tablename__ = "child_web_bundle"
    __table_args__ = (UniqueConstraint("child_id", "bundle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    bundle: Mapped[str] = mapped_column(String(64))


class ChildDomain(FamilyScoped, Base):
    """Eigene Domain-Regel: allow (Whitelist/Ausnahme) bzw. deny (Blacklist)."""
    __tablename__ = "child_domain"
    __table_args__ = (UniqueConstraint("child_id", "domain", "action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    action: Mapped[str] = mapped_column(String(8))  # allow | deny


class ChildBlocklist(FamilyScoped, Base):
    """Gewählte Sperrliste (Blacklist-Modus)."""
    __tablename__ = "child_blocklist"
    __table_args__ = (UniqueConstraint("child_id", "list_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    list_id: Mapped[str] = mapped_column(String(64))


class BlocklistState(Base):
    """Download-Stand einer Sperrliste (Datei unter <data>/lists/<id>.txt)."""
    __tablename__ = "blocklist_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(String(255), default="")


class AppUsage(FamilyScoped, Base):
    """Programmzeit (geöffnet während gezählter Bildschirmzeit) je (Gerät, User, Tag, App)."""
    __tablename__ = "app_usage"
    __table_args__ = (UniqueConstraint("device_id", "username", "day", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL = Gerät entfernt; Statistik je Kind bleibt erhalten
    device_id: Mapped[int | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(Matchable("app_usage.username"))
    day: Mapped[date] = mapped_column(Date, index=True)
    key: Mapped[str] = mapped_column(Matchable("app_usage.key"))
    child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(Sealed("app_usage.name"), default="")  # Spiele: Name vom Agenten


class PairingCode(FamilyScoped, Base):
    """Einmaliger Kopplungscode (8 Zeichen, 10 min gültig). Gespeichert wird nur der Hash."""
    __tablename__ = "pairing_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_name: Mapped[str] = mapped_column(Sealed("pairing_code.device_name"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), nullable=True)


class Message(FamilyScoped, Base):
    """Chat-Eintrag. ``kind='extension_request'`` = Verlängerungsanfrage."""
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    from_child: Mapped[bool] = mapped_column(Boolean)
    sender_parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="text")  # text | extension_request
    body: Mapped[str] = mapped_column(Sealed("message.body"), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # Nur für extension_request:
    requested_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    request_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # pending|approved|rejected|expired
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    request_target: Mapped[str | None] = mapped_column(String(8), nullable=True)  # games = Spielzeit
    # Herkunft vom Agenten (Idempotenz bei Offline-Warteschlange)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    client_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Nur für access_request (Freigabe-Anfrage für Programm/Website):
    access_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)   # program | domain
    access_key: Mapped[str | None] = mapped_column(Matchable("message.access_key"), nullable=True)
    access_name: Mapped[str | None] = mapped_column(Sealed("message.access_name"), nullable=True)
    access_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)  # permanent | today


class NintendoLink(FamilyScoped, Base):
    """Verknüpfung mit dem Nintendo-Konto der Familie (Switch-Spielzeit, experimentell – app.nintendo).

    ``session_token`` ist ein Vollzugriff auf das Nintendo-Konto: nur verschlüsselt, nie im Export, nie im Log."""
    __tablename__ = "nintendo_link"
    __table_args__ = (UniqueConstraint("family_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_token: Mapped[str | None] = mapped_column(Sealed("nintendo_link.session_token"), nullable=True)
    # Einrichtung läuft: PKCE-Verifier und Login-Link (zwei Anfragen, auch über einen Neustart hinweg)
    pending_verifier: Mapped[str | None] = mapped_column(Sealed("nintendo_link.pending_verifier"), nullable=True)
    pending_url: Mapped[str | None] = mapped_column(Sealed("nintendo_link.pending_url"), nullable=True)
    pending_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="")   # "" | ok | error | reauth
    error: Mapped[str] = mapped_column(String(32), default="")    # Fehlerart (network, http_503, update_required …)
    failures: Mapped[int] = mapped_column(Integer, default=0)     # Fehler in Folge (Backoff)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NintendoConsole(FamilyScoped, Base):
    """Switch-Konsole mit dem letzten bekannten Stand von heute (Werte gelten je Konsole)."""
    __tablename__ = "nintendo_console"
    __table_args__ = (UniqueConstraint("family_id", "device_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_key: Mapped[str] = mapped_column(Matchable("nintendo_console.device_key"))   # device_id bei Nintendo
    name: Mapped[str] = mapped_column(Sealed("nintendo_console.name"), default="")
    model: Mapped[str] = mapped_column(String(16), default="")
    day: Mapped[date | None] = mapped_column(Date, nullable=True)            # Tag, für den die Werte gelten
    used_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)       # NULL = kein Limit
    remaining_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)   # NULL = unbegrenzt/unbekannt
    last_sync: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)     # zuletzt neue Spielzeit abgerufen
    stale: Mapped[bool] = mapped_column(Boolean, default=False)              # letzter Abruf fehlgeschlagen
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    players: Mapped[list[NintendoPlayer]] = relationship(
        back_populates="console", cascade="all, delete-orphan", order_by="NintendoPlayer.id")


class NintendoPlayer(FamilyScoped, Base):
    """Spielerprofil auf einer Konsole; ``child_id`` NULL = keinem Kind zugeordnet (wird nur angezeigt)."""
    __tablename__ = "nintendo_player"
    __table_args__ = (UniqueConstraint("console_id", "player_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    console_id: Mapped[int] = mapped_column(ForeignKey("nintendo_console.id", ondelete="CASCADE"), index=True)
    player_key: Mapped[str] = mapped_column(Matchable("nintendo_player.player_key"))   # playerId bei Nintendo
    nickname: Mapped[str] = mapped_column(Sealed("nintendo_player.nickname"), default="")
    child_id: Mapped[int | None] = mapped_column(ForeignKey("child.id", ondelete="SET NULL"), nullable=True)

    console: Mapped[NintendoConsole] = relationship(back_populates="players")


class NintendoUsage(FamilyScoped, Base):
    """Spielzeit eines Profils je Tag (nur Anzeige – zählt nicht ins Kontingent)."""
    __tablename__ = "nintendo_usage"
    __table_args__ = (UniqueConstraint("player_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL = Konsole getrennt; Statistik je Kind bleibt erhalten
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("nintendo_player.id", ondelete="SET NULL"), nullable=True)
    child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    apps: Mapped[str] = mapped_column(Sealed("nintendo_usage.apps"), default="")   # JSON [[Name, Minuten], …]
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


@event.listens_for(Parent.email, "set")
def _email_index(target: Parent, value, _old, _initiator) -> None:
    """Suchwert immer passend zur Adresse (auch beim Anlegen per Konstruktor)."""
    from .crypto import email_index
    target.email_index = email_index(value or "")


# Mandantentrennung registrieren (Ereignisse auf Session), sobald die Modelle vollständig sind
from . import tenant  # noqa: E402,F401
