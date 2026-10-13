"""Datenmodell. Alle Zeitstempel als naive UTC-Datetimes."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Parent(Base):
    """Eltern-Account; alle Accounts sind gleichberechtigt."""
    __tablename__ = "parent"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str] = mapped_column(String(64))
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # Replay-Schutz
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
    kind: Mapped[str] = mapped_column(String(16), default="login")  # login | enroll
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


class Child(Base):
    """Kind – zentrale Einheit, trägt alle Policies."""
    __tablename__ = "child"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    weekdays_same: Mapped[bool] = mapped_column(Boolean, default=True)  # UI: Werktage/Wochenende gleich
    web_filter_mode: Mapped[str] = mapped_column(String(16), default="whitelist")  # whitelist | blacklist
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    rules: Mapped[list[WeekdayRule]] = relationship(
        back_populates="child", cascade="all, delete-orphan", order_by="WeekdayRule.weekday")


class WeekdayRule(Base):
    """Wochenvorlage: je Wochentag Kontingent + erlaubtes Zeitfenster (Europe/Berlin)."""
    __tablename__ = "weekday_rule"
    __table_args__ = (UniqueConstraint("child_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Montag … 6 = Sonntag
    quota_minutes: Mapped[int] = mapped_column(Integer, default=60)
    window_start: Mapped[time] = mapped_column(Time, default=time(8, 0))
    window_end: Mapped[time] = mapped_column(Time, default=time(20, 0))

    child: Mapped[Child] = relationship(back_populates="rules")


class DayException(Base):
    """Tagesausnahme (Verlängerung/Gutschrift), gilt nur für ``day``."""
    __tablename__ = "day_exception"
    __table_args__ = (UniqueConstraint("child_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    extra_minutes: Mapped[int] = mapped_column(Integer, default=0)
    window_end_override: Mapped[time | None] = mapped_column(Time, nullable=True)


class Device(Base):
    __tablename__ = "device"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    hostname: Mapped[str] = mapped_column(String(255), default="")
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

    users: Mapped[list[DeviceUser]] = relationship(
        back_populates="device", cascade="all, delete-orphan", order_by="DeviceUser.username")


class DeviceUser(Base):
    """Zuordnung (Gerät, Linux-User) -> Kind. ``child_id`` NULL = nicht eingeschränkt."""
    __tablename__ = "device_user"
    __table_args__ = (UniqueConstraint("device_id", "username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    uid: Mapped[int] = mapped_column(Integer)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    present: Mapped[bool] = mapped_column(Boolean, default=True)  # zuletzt vom Agenten gemeldet
    child_id: Mapped[int | None] = mapped_column(ForeignKey("child.id", ondelete="SET NULL"), nullable=True)

    device: Mapped[Device] = relationship(back_populates="users")
    child: Mapped[Child | None] = relationship()


class UsageRecord(Base):
    """Absolute Nutzungszeit je (Gerät, Linux-User, Tag), wie vom Agenten gemeldet."""
    __tablename__ = "usage_record"
    __table_args__ = (UniqueConstraint("device_id", "username", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(String(64))
    day: Mapped[date] = mapped_column(Date, index=True)
    child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PairingCode(Base):
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


class Message(Base):
    """Chat-Eintrag. ``kind='extension_request'`` = Verlängerungsanfrage."""
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("child.id", ondelete="CASCADE"), index=True)
    from_child: Mapped[bool] = mapped_column(Boolean)
    sender_parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("parent.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="text")  # text | extension_request
    body: Mapped[str] = mapped_column(Text, default="")
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
    # Herkunft vom Agenten (Idempotenz bei Offline-Warteschlange)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), nullable=True)
    client_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
