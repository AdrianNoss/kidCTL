"""Datenmodell. Alle Zeitstempel als naive UTC-Datetimes."""

from __future__ import annotations

import json

from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, Time, UniqueConstraint, func,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from .crypto import Matchable, Sealed
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Family(Base):
    """Mandant: eine Familie (Haushalt). Im Einzelbetrieb gibt es genau eine."""
    __tablename__ = "family"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Datenschlüssel der Familie, mit dem Hauptschlüssel eingepackt (app.crypto); NULL = gelöscht/unlesbar
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


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


class Parent(Base):
    """Eltern-Account; gehört über ``Membership`` zu genau einer Familie."""
    __tablename__ = "parent"
    # E-Mail systemweit eindeutig (ohne Groß/klein); leere Adressen (Einzelbetrieb) sind erlaubt
    __table_args__ = (Index("ix_parent_email_lower", func.lower(text("email")), unique=True,
                            postgresql_where=text("email <> ''")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str] = mapped_column(String(64))
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # Replay-Schutz
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    email: Mapped[str] = mapped_column(String(255), default="")  # Anmeldung, „Passwort vergessen“; SaaS: Pflicht
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # Bestätigungslink
    report_interval: Mapped[str] = mapped_column(String(16), default="")  # Nutzungsbericht: "" | weekly | monthly
    report_sent: Mapped[str] = mapped_column(String(16), default="")      # zuletzt gesendeter Zeitraum (Schlüssel)
    language: Mapped[str] = mapped_column(String(8), default="")           # "" = automatisch (Browser) | de | en
    detected_language: Mapped[str] = mapped_column(String(8), default="")  # zuletzt erkannt (für Mails/Push)


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

    parent: Mapped[Parent] = relationship()


class LoginAttempt(Base):
    """Fehlgeschlagene Logins/Kopplungen für Rate-Limiting."""
    __tablename__ = "login_attempt"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="login")  # login | totp | reset | enroll
    ip: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    rules: Mapped[list[WeekdayRule]] = relationship(
        back_populates="child", cascade="all, delete-orphan", order_by="WeekdayRule.weekday")


class WeekdayRule(FamilyScoped, Base):
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

    child: Mapped[Child] = relationship(back_populates="rules")


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
    integrity_json: Mapped[str] = mapped_column(Text, default="")  # letzte IntegrityReport.checks
    integrity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    update_alerted: Mapped[str] = mapped_column(String(32), default="")  # Version, zu der schon gewarnt wurde

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
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(Matchable("usage_record.username"))
    day: Mapped[date] = mapped_column(Date, index=True)
    child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    game_seconds: Mapped[int] = mapped_column(Integer, default=0)  # davon mit laufendem Spiel
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
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
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
    device_name: Mapped[str] = mapped_column(String(128))
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


# Mandantentrennung registrieren (Ereignisse auf Session), sobald die Modelle vollständig sind
from . import tenant  # noqa: E402,F401
