"""Konfiguration ausschließlich über Umgebungsvariablen (.env)."""

from __future__ import annotations

import logging
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
    webauthn_rp_id: str = ""         # WEBAUTHN_RP_ID; leer: Host aus BASE_URL (Passkeys sind daran gebunden)
    session_days: int = 30
    login_max_failures: int = 5        # je IP; „Passwort richtig, Code falsch“ auch je Benutzer
    login_user_max_failures: int = 50  # je Benutzer: hoch, sonst könnte jeder ein Elternteil aussperren
    login_window_minutes: int = 15
    pairing_code_minutes: int = 10
    source_url: str = "https://github.com/AdrianNoss/kidCTL"   # AGPL §13: Quelltext des laufenden Servers
    vapid_private_key: str = ""   # Web Push; erzeugen mit `python -m app.cli vapid-keys`
    vapid_public_key: str = ""
    vapid_subject: str = ""       # mailto:… oder https://…
    # E-Mail (Passwort vergessen, Hinweise bei Änderungen); ohne SMTP_HOST kein Mailversand
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_security: str = "starttls"   # starttls | ssl | none
    dev: bool = False
    mode: str = "single"   # single = eine Familie (Self-Hosting) | saas = viele Familien
    master_key: str = ""   # KIDCTL_MASTER_KEY: verschlüsselt die Schlüssel der Familien (app.crypto)
    registration: str = "closed"   # REGISTRATION: open | invite | closed (Einzelbetrieb immer closed)
    trial_days: int = 30            # KIDCTL_TRIAL_DAYS: Testphase neuer Familien (SaaS)
    billing_webhook_secret: str = ""   # BILLING_WEBHOOK_SECRET: Webhook des Zahlungsanbieters (leer = aus)
    update_check: bool = True         # UPDATE_CHECK: täglich kidctl.de/version.json abrufen (Hinweis auf neue Version)
    support_notify_email: str = ""    # SUPPORT_NOTIFY_EMAIL: Sammeladresse für Ticket-Hinweise (optional)
    feature_request_email: str = ""   # FEATURE_REQUEST_EMAIL: Empfänger für „Feature vorschlagen“ (leer = aus)
    ticket_retention_days: int = 365  # TICKET_RETENTION_DAYS: geschlossene Tickets danach löschen
    invoice_tax_mode: str = "small_business"   # INVOICE_TAX_MODE: small_business | standard
    invoice_number_format: str = "RE-{YYYY}-{NNNN}"   # INVOICE_NUMBER_FORMAT
    invoice_due_days: int = 14        # INVOICE_DUE_DAYS
    trusted_proxies: tuple = ()    # TRUSTED_PROXIES: nur von dort zählt X-Forwarded-For („*“ = von überall)
    admin_allow_nets: tuple = ()   # ADMIN_ALLOW_NETS: Verwaltungsbereich nur aus diesen Netzen (leer = alle)
    relocate_to: str = ""  # KIDCTL_RELOCATE_TO: Server-Umzug – Agenten wechseln, wenn der neue Server sie anerkennt

    @property
    def saas(self) -> bool:
        return self.mode == "saas"

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_private_key and self.vapid_public_key and self.vapid_subject)

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def feature_requests(self) -> bool:
        """„Feature vorschlagen“ nur mit Empfänger und SMTP – ohne beides verlässt nichts die Instanz."""
        return bool(self.feature_request_email and self.mail_enabled)


DEV_DATABASE_URL = "postgresql+psycopg://kidctl:kidctl-dev@127.0.0.1:55432/kidctl"


def _database_url(dev: bool) -> str:
    """PostgreSQL (seit 2026-11, Etappe 1 Multi-Tenant). SQLite wird nur noch per ``cli import-sqlite`` gelesen."""
    url = os.environ.get("DATABASE_URL", "") or (DEV_DATABASE_URL if dev else "")
    if not url:
        raise RuntimeError("DATABASE_URL fehlt (in .env setzen, z. B. postgresql+psycopg://kidctl:…@db:5432/kidctl).")
    if url.startswith("sqlite"):
        raise RuntimeError("SQLite wird nicht mehr unterstützt. Daten übernehmen: python -m app.cli import-sqlite "
                           "<pfad/kidctl.db> --apply (Anleitung: https://kidctl.de/docs/postgresql-migration/,"
                           " Datei docs/postgresql-migration.md).")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


DEV_MASTER_KEY = "ZGV2LW51ci1sb2thbC1rZWluLWVjaHRlci1zY2hsdWU"   # nur Entwicklung/Tests (32 Byte)


def _master_key(dev: bool) -> str:
    key = os.environ.get("KIDCTL_MASTER_KEY", "").strip()
    if not key and not dev:
        raise RuntimeError("KIDCTL_MASTER_KEY fehlt (in .env setzen; erzeugen mit `python -m app.cli master-key`). "
                           "Ohne ihn sind die verschlüsselten Daten nicht lesbar – getrennt sichern!")
    return key or DEV_MASTER_KEY


def _relocate_to() -> str:
    url = os.environ.get("KIDCTL_RELOCATE_TO", "").strip().rstrip("/")
    if url and not url.startswith("https://"):
        raise RuntimeError("KIDCTL_RELOCATE_TO muss mit https:// beginnen.")
    return url


def _registration(mode: str) -> str:
    if mode != "saas":
        return "closed"   # Einzelbetrieb: eine Familie, erster Account per CLI
    value = os.environ.get("REGISTRATION", "invite").strip().lower() or "invite"
    if value not in ("open", "invite", "closed"):
        raise RuntimeError(f"REGISTRATION={value!r} – erlaubt sind open, invite und closed.")
    return value


def _tax_mode() -> str:
    mode = os.environ.get("INVOICE_TAX_MODE", "small_business").strip().lower() or "small_business"
    if mode not in ("small_business", "standard"):
        raise RuntimeError(f"INVOICE_TAX_MODE={mode!r} – erlaubt sind small_business und standard.")
    return mode


def _number_format() -> str:
    fmt = os.environ.get("INVOICE_NUMBER_FORMAT", "").strip() or "RE-{YYYY}-{NNNN}"
    if "{NNNN}" not in fmt or "{YYYY}" not in fmt or len(fmt) > 24:
        raise RuntimeError("INVOICE_NUMBER_FORMAT braucht {YYYY} und {NNNN} (z. B. RE-{YYYY}-{NNNN}).")
    return fmt


def _admin_nets() -> tuple:
    import ipaddress
    raw = os.environ.get("ADMIN_ALLOW_NETS", "")
    try:
        return tuple(ipaddress.ip_network(n.strip(), strict=False) for n in raw.split(",") if n.strip())
    except ValueError as e:
        raise RuntimeError(f"ADMIN_ALLOW_NETS ungültig ({e}) – z. B. 192.168.1.0/24,2001:db8::/32") from e


DEFAULT_TRUSTED_PROXIES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1/128,fc00::/7"


def _trusted_proxies() -> tuple:
    import ipaddress
    raw = os.environ.get("TRUSTED_PROXIES", "").strip() or DEFAULT_TRUSTED_PROXIES
    if raw == "*":
        return ("*",)
    try:
        return tuple(ipaddress.ip_network(n.strip(), strict=False) for n in raw.split(",") if n.strip())
    except ValueError as e:
        raise RuntimeError(f"TRUSTED_PROXIES ungültig ({e}) – z. B. 172.18.0.2/32") from e


def _mode() -> str:
    mode = os.environ.get("KIDCTL_MODE", "single").strip().lower() or "single"
    if mode not in ("single", "saas"):
        raise RuntimeError(f"KIDCTL_MODE={mode!r} – erlaubt sind single und saas.")
    return mode


def load_settings() -> Settings:
    dev = _bool("KIDCTL_DEV", False)
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        if not dev:
            raise RuntimeError("SECRET_KEY fehlt (in .env setzen). Nur mit KIDCTL_DEV=1 optional.")
        secret = "dev-insecure-secret"
    if not dev and len(secret) < 32:
        # kein Abbruch: Ein neuer Schlüssel macht alle Geräte-Token ungültig, das entscheidet der Betreiber
        logging.getLogger("kidctl.config").warning("SECRET_KEY ist kürzer als 32 Zeichen – bitte zufällig und länger "
                                                   "wählen (Achtung: Geräte müssen danach neu gekoppelt werden)")
    data_dir = os.environ.get("KIDCTL_DATA_DIR", "/data")
    return Settings(
        secret_key=secret,
        database_url=_database_url(dev),
        data_dir=data_dir,
        cookie_secure=_bool("COOKIE_SECURE", not dev),
        base_url=os.environ.get("BASE_URL", "https://app.kidctl.de").rstrip("/"),
        timezone=os.environ.get("TZ_HOME", "Europe/Berlin"),
        totp_issuer=os.environ.get("TOTP_ISSUER", ""),   # leer: Name aus dem Branding
        webauthn_rp_id=os.environ.get("WEBAUTHN_RP_ID", "").strip().lower(),
        source_url=os.environ.get("SOURCE_URL", "https://github.com/AdrianNoss/kidCTL"),
        mode=_mode(),
        registration=_registration(_mode()),
        master_key=_master_key(dev),
        relocate_to=_relocate_to(),
        trusted_proxies=_trusted_proxies(),
        admin_allow_nets=_admin_nets(),
        invoice_tax_mode=_tax_mode(),
        invoice_number_format=_number_format(),
        invoice_due_days=int(os.environ.get("INVOICE_DUE_DAYS", "14") or 14),
        support_notify_email=os.environ.get("SUPPORT_NOTIFY_EMAIL", "").strip(),
        feature_request_email=os.environ.get("FEATURE_REQUEST_EMAIL", "").strip(),
        update_check=_bool("UPDATE_CHECK", True),
        ticket_retention_days=int(os.environ.get("TICKET_RETENTION_DAYS", "365") or 365),
        trial_days=int(os.environ.get("KIDCTL_TRIAL_DAYS", "30") or 30),
        billing_webhook_secret=os.environ.get("BILLING_WEBHOOK_SECRET", "").strip(),
        vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY", ""),
        vapid_public_key=os.environ.get("VAPID_PUBLIC_KEY", ""),
        vapid_subject=os.environ.get("VAPID_SUBJECT", ""),
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587") or 587),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("SMTP_FROM", ""),
        smtp_security=os.environ.get("SMTP_SECURITY", "starttls").strip().lower(),
        dev=dev,
    )


settings = load_settings()
