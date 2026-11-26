"""Tägliche Übersicht per Mail an ``FEATURE_INBOX_REPORT_TO`` (nur Zahlen und Issue-Links, keine Mailadressen)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage


def text(events: list[tuple[str, str]], repo: str) -> tuple[str, str]:
    new = [d for k, d in events if k == "new"]
    dup = [d for k, d in events if k == "duplicate"]
    unsorted = sum(1 for k, _ in events if k == "unsorted")
    errors = sum(1 for k, _ in events if k == "error")
    subject = f"[kidCTL Feature-Inbox] {len(new)} neu, {len(dup)} Duplikat-Verdacht, {unsorted} unsortiert"
    lines = ["Übersicht der letzten 24 Stunden", ""]
    lines.append(f"Neue Requests: {len(new)}")
    lines += [f"  - https://github.com/{repo}/issues/{n}" for n in new]
    lines.append(f"Mit Duplikat-Verdacht: {len(dup)}")
    lines += [f"  - #{d}" for d in dup]
    lines.append(f"Unsortiert (kein v1-Format): {unsorted}")
    if errors:
        lines.append(f"Fehler beim Verarbeiten: {errors} (siehe Container-Log)")
    lines += ["", "Freigabe: Label needs-triage entfernen und approved setzen."]
    return subject, "\n".join(lines) + "\n"


def send(cfg, subject: str, body: str, smtp_factory=None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_from
    msg["To"] = cfg.report_to
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if smtp_factory is not None:
        smtp = smtp_factory()
    elif cfg.smtp_security == "ssl":
        smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=30)
    else:
        smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
        if cfg.smtp_security == "starttls":
            smtp.starttls(context=ctx)
    with smtp:
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)
