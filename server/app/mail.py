"""E-Mail-Versand per SMTP (stdlib). Ohne Konfiguration: kein Versand; im Entwicklungsmodus wird die
Mail stattdessen geloggt (zum lokalen Testen von „Passwort vergessen“)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import settings

log = logging.getLogger("kidctl.mail")
TIMEOUT = 20


def send(to: str, subject: str, body: str, html: str | None = None,
         attachments: list[tuple[str, bytes, str]] | None = None, headers: dict[str, str] | None = None,
         inline: list[tuple[str, bytes, str]] | None = None, branded: bool = False) -> bool:
    """Mail senden (``html``: zusätzlich als HTML-Alternative; ``attachments``: (Dateiname, Inhalt, MIME-Typ));
    ``False`` bei Fehler oder ohne SMTP (Fehler werden geloggt, nie an Nutzer gezeigt)."""
    if not branded:   # Mails aus Templates (app.mailer) tragen Name und Fußzeile schon selbst
        from . import branding
        subject, body, html = branding.mail_parts(subject, body, html)   # eigener Name + „powered by KidCTL“
    if not settings.mail_enabled:
        if settings.dev:
            log.info("Mail (nicht gesendet, KIDCTL_DEV) an %s: %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1].strip(" >") or None)
    for key, value in (headers or {}).items():   # z. B. List-Unsubscribe beim Newsletter
        msg[key] = value
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
        if inline:   # Bilder im HTML (cid:…) – HTML und Bilder als multipart/related
            html_part = msg.get_payload()[1]
            for cid, data, mimetype in inline:
                maintype, _sep, subtype = mimetype.partition("/")
                html_part.add_related(data, maintype=maintype, subtype=subtype, cid=f"<{cid}>",
                                      filename=f"{cid}.{subtype}", disposition="inline")
    for filename, data, mimetype in attachments or []:
        maintype, _sep, subtype = mimetype.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    try:
        ctx = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT, context=ctx)
        else:
            smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT)
        with smtp:
            if settings.smtp_security == "starttls":
                smtp.starttls(context=ctx)
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        log.info("Mail an %s gesendet: %s", to, subject)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.error("Mail an %s nicht gesendet: %s", to, e)
        return False
