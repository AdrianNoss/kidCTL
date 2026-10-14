"""Konfiguration ausschließlich über Umgebungsvariablen (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    data_dir: str
    cookie_secure: bool
    base_url: str
    timezone: str
    totp_issuer: str
    session_days: int = 30
    login_max_failures: int = 5
    login_window_minutes: int = 15
    pairing_code_minutes: int = 10
    vapid_private_key: str = ""   # Web Push; erzeugen mit `python -m app.cli vapid-keys`
    vapid_public_key: str = ""
    vapid_subject: str = ""       # mailto:… oder https://…

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_private_key and self.vapid_public_key and self.vapid_subject)


def load_settings() -> Settings:
    dev = _bool("KIDCTL_DEV", False)
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        if not dev:
            raise RuntimeError("SECRET_KEY fehlt (in .env setzen). Nur mit KIDCTL_DEV=1 optional.")
        secret = "dev-insecure-secret"
    data_dir = os.environ.get("KIDCTL_DATA_DIR", "/data")
    return Settings(
        secret_key=secret,
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{data_dir}/kidctl.db"),
        data_dir=data_dir,
        cookie_secure=_bool("COOKIE_SECURE", not dev),
        base_url=os.environ.get("BASE_URL", "https://kids.nossco.de").rstrip("/"),
        timezone=os.environ.get("TZ_HOME", "Europe/Berlin"),
        totp_issuer=os.environ.get("TOTP_ISSUER", "KidCTL"),
        vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY", ""),
        vapid_public_key=os.environ.get("VAPID_PUBLIC_KEY", ""),
        vapid_subject=os.environ.get("VAPID_SUBJECT", ""),
    )


settings = load_settings()
