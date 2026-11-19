"""Newsletter: Neuigkeiten (neue Versionen, Funktionen) an alle Familien mit Zustimmung.

Opt-in je Familie (``Family.newsletter_at``), Standard aus. Jedes Elternteil mit bestätigter Adresse bekommt die Mail in
seiner Sprache (Englisch nur, wenn ein englischer Text da ist). Jede Mail hat einen Abmeldelink (HMAC-signiert,
ohne Anmeldung) und die Header ``List-Unsubscribe``/``List-Unsubscribe-Post`` (Abmelden mit einem Klick, RFC 8058).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import accounts, i18n, mail
from .config import settings
from .i18n import _
from .models import Family, Membership, Newsletter, Parent, utcnow
from .tenant import family_session, platform_session

log = logging.getLogger("kidctl.newsletter")


# ---------- Abmeldelink ----------

def _sig(family_id: int) -> str:
    return hmac.new(settings.secret_key.encode(), f"newsletter-unsubscribe:{family_id}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(family_id: int) -> str:
    return f"{settings.base_url}/newsletter/unsubscribe/{family_id}/{_sig(family_id)}"


def valid_signature(family_id: int, sig: str) -> bool:
    return hmac.compare_digest(_sig(family_id), sig)


def unsubscribe(family_id: int) -> bool:
    """Zustimmung der Familie entfernen; ``True``, wenn sie bestand."""
    with platform_session() as db:
        family = db.get(Family, family_id)
        if family is None or family.newsletter_at is None:
            return False
        family.newsletter_at = None
        db.commit()
        log.info("Familie %s hat den Newsletter abbestellt", family_id)
        return True


# ---------- Empfänger und Versand ----------

def subscribed_families(db: Session) -> list[int]:
    return list(db.scalars(select(Family.id).where(Family.newsletter_at.is_not(None), Family.wrapped_key.is_not(None))
                           .order_by(Family.id)))


def _recipients(family_id: int) -> list[tuple[str, str, str]]:
    """(E-Mail, Sprache, Anrede) aller Eltern der Familie mit nutzbarer Adresse."""
    with family_session(family_id) as db:
        parents = db.scalars(select(Parent).join(Membership, Membership.parent_id == Parent.id)).all()
        return [(p.email, i18n.parent_language(p), p.display_name or p.username)
                for p in parents if accounts.email_usable(p)]


def mail_ctx(nl: Newsletter, lang: str, name: str, family_id: int | None) -> tuple[str, dict]:
    """(Sprache, Variablen) der Newsletter-Mail: Englisch nur mit englischem Text, sonst Deutsch."""
    english = lang == "en" and nl.subject_en.strip() and nl.body_en.strip()
    subject, body = (nl.subject_en, nl.body_en) if english else (nl.subject_de, nl.body_de)
    from . import mdmail
    return ("en" if english else "de"), dict(name=name, nl_subject=subject.strip(), body=body.strip(),
                                             body_html=mdmail.to_html(body), preheader_text=mdmail.preheader(body),
                                             unsubscribe_url=unsubscribe_url(family_id) if family_id is not None else "")


def compose(nl: Newsletter, lang: str, name: str, family_id: int | None) -> tuple[str, str]:
    """Betreff und Textfassung (für Tests und Vorschau)."""
    from . import mailer
    use, ctx = mail_ctx(nl, lang, name, family_id)
    r = mailer.render("newsletter", use, **ctx)
    return r.subject, r.text


def send_test(nl: Newsletter, to: str) -> int:
    """Testversand an den Betreiber: beide Sprachen (sofern vorhanden). Anzahl gesendeter Mails."""
    sent = 0
    from . import mailer
    for lang in ("de", "en") if nl.subject_en.strip() and nl.body_en.strip() else ("de",):
        use, ctx = mail_ctx(nl, lang, to, None)
        ctx["nl_subject"] = "[Test] " + ctx["nl_subject"]
        sent += bool(mailer.send(to, "newsletter", use, **ctx))
    return sent


def deliver(newsletter_id: int) -> None:
    """Versand im Hintergrund: an jedes Elternteil der Familien mit Zustimmung, danach Status „sent“."""
    with platform_session() as db:
        nl = db.get(Newsletter, newsletter_id)
        if nl is None or nl.status != "sending":
            return
        families = subscribed_families(db)
        db.expunge(nl)
    sent = 0
    for family_id in families:
        try:
            recipients = _recipients(family_id)
        except Exception:   # eine kaputte Familie hält den Versand nicht auf
            log.exception("Newsletter %s: Empfänger der Familie %s nicht lesbar", newsletter_id, family_id)
            continue
        url = unsubscribe_url(family_id)
        from . import mailer
        for email, lang, name in recipients:
            use, ctx = mail_ctx(nl, lang, name, family_id)
            if mailer.send(email, "newsletter", use, headers={"List-Unsubscribe": f"<{url}>",
                                                              "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
                           **ctx):
                sent += 1
    with platform_session() as db:
        row = db.get(Newsletter, newsletter_id)
        row.status, row.sent_at, row.families, row.recipients = "sent", utcnow(), len(families), sent
        db.commit()
    log.info("Newsletter %s an %d Familien versendet (%d Mails)", newsletter_id, len(families), sent)
