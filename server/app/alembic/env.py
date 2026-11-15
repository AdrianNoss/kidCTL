"""Alembic-Umgebung: Schema aus app.models, Verbindung aus der Konfiguration (alembic_config in app.db)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (Tabellen registrieren)
from app.db import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline-Migrationen werden nicht unterstützt")
run_migrations_online()
