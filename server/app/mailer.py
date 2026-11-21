"""Einheitliche HTML-Mails: jede Mail ist ein Template in ``templates/mail/`` (erbt von ``mail/base.html``) und hat
immer eine Textfassung (``mail/base.txt`` aus denselben Variablen).

Ein Mail-Template legt oben Variablen fest, die Basis-Layout und Textfassung lesen:

- ``subject`` (ohne Namen – den setzt das System davor), ``preheader`` (Vorschauzeile), ``heading`` (Überschrift)
- ``greeting``, ``paragraphs`` (Liste), ``action`` (Beschriftung, URL) als Button, ``after`` (Liste nach dem Button)
- ``reason`` (Grund der Mail im Fuß), optional ``unsubscribe`` (URL)
- eigene Inhalte über ``{% block content %}`` (HTML) und ``text_template`` (Textfassung), z. B. Nutzungsbericht

Das Logo hängt als Inline-Bild (CID) an – keine externen Bilder, kein Tracking.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from . import branding, i18n, mail
from .config import settings

log = logging.getLogger("kidctl.mailer")

LOGO_CID = "logo@kidctl"
LOGO_MAX = (400, 96)                 # Pixel (doppelte Dichte; angezeigt in halber Größe)
DEFAULT_LOGO = Path(__file__).parent / "static" / "mail-logo.png"   # aus packaging/arch/kidctl.svg (rsvg-convert)
PROJECT_SITE = branding.PROJECT_SITE
MAX_HTML = 100 * 1024                # Gmail kürzt größere HTML-Teile


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


# ---------- Logo ----------

_logo_cache: dict[int, tuple[bytes, int, int]] = {}


def logo() -> tuple[bytes, int, int]:
    """(PNG, Anzeigebreite, Anzeigehöhe): eigenes Logo (Whitelabel), sonst das KidCTL-Symbol."""
    b = branding.current()
    if b.version in _logo_cache:
        return _logo_cache[b.version]
    data = branding.image("logo") if b.has_logo else None
    try:
        from PIL import Image
        if data:
            img = Image.open(io.BytesIO(data))
            img.thumbnail(LOGO_MAX)
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            result = (buf.getvalue(), max(1, img.width // 2), max(1, img.height // 2))
        else:
            data = DEFAULT_LOGO.read_bytes()
            w, h = Image.open(io.BytesIO(data)).size
            result = (data, w // 2, h // 2)
    except Exception:   # Bild kaputt: Standardlogo
        log.exception("Mail-Logo nicht lesbar")
        result = (DEFAULT_LOGO.read_bytes(), 48, 48)
    _logo_cache.clear()
    _logo_cache[b.version] = result
    return result


# ---------- Fußzeile ----------

def footer() -> dict:
    """Links und Anbieterzeile für den Fuß (in der aktuellen Sprache). Leere Angaben entfallen."""
    from .models import Branding
    from .tenant import platform_session
    row = None
    try:
        with platform_session() as db:
            row = db.get(Branding, 1)
            if row is not None:
                db.expunge(row)
    except Exception:
        log.exception("Anbieter-Angaben nicht lesbar")
    lang_path = "de/" if i18n.current() == "de" else ""
    _ = i18n.gettext
    links = [(_("Neuigkeiten"), (row.news_url if row and row.news_url else f"{PROJECT_SITE}{lang_path}news/")),
             (_("Anleitungen"), f"{PROJECT_SITE}{lang_path}docs/")]
    if settings.saas:
        links.append((_("Support"), f"{settings.base_url}/support"))
    if row and row.imprint_url:
        links.append((_("Impressum"), row.imprint_url))
    if row and row.privacy_url:
        links.append((_("Datenschutz"), row.privacy_url))
    provider = [x for x in ((row.provider_name, row.provider_address, row.provider_email) if row else ()) if x]
    return {"links": links, "provider": provider}


# ---------- Rendern und Senden ----------

def _env():
    from .web import templates
    return templates.env


def render(template: str, lang: str = "de", /, **ctx) -> Rendered:
    """Mail ``templates/mail/<name>.html`` in ``lang`` rendern: Betreff, Textfassung, HTML."""
    env = _env()
    with i18n.use(lang):
        b = branding.current()
        _png, logo_w, logo_h = logo()
        base = dict(base_url=settings.base_url, logo_cid=LOGO_CID, logo_w=logo_w, logo_h=logo_h,
                    footer=footer(), saas=settings.saas, **ctx)
        tmpl = env.get_template(f"mail/{template}.html")
        module = tmpl.make_module(base)
        exported = {k: getattr(module, k) for k in dir(module) if not k.startswith("_")}
        full = base | exported
        html = tmpl.render(full)
        text = env.get_template("mail/base.txt").render(full)
        subject = str(exported.get("subject", ""))
        if not exported.get("plain_subject"):   # Newsletter: eigener Betreff ohne Namen davor
            subject = f"{b.name}: {subject}"
    return Rendered(subject=subject, text=text, html=html)


def preview_html(html: str) -> str:
    """HTML für die Vorschau im abgeschotteten Rahmen (``sandbox``, eigener Ursprung): Logo als ``data:``-URI statt
    CID – eine Adresse des Servers erlaubt die CSP dort nicht (``img-src 'self'`` passt nicht auf den Rahmen)."""
    import base64
    png, _w, _h = logo()
    return html.replace(f"cid:{LOGO_CID}", "data:image/png;base64," + base64.b64encode(png).decode())


_STYLE_ATTR = re.compile(r'(<[a-zA-Z][^<>]*?)\sstyle="([^"]*)"')
_STYLE_TAG = re.compile(r"<style(?=[\s>])")


def csp_safe(html: str, nonce: str) -> str:
    """Mail-HTML für Vorschauen im WebUI, dessen CSP keine Inline-Styles erlaubt: ``style``-Attribute werden zu
    ``data-ks``-Attributen mit Regeln in einem <style>-Block, alle <style>-Blöcke bekommen die ``nonce`` (die Antwort
    setzt ``middleware.csp(nonce)``). ``!important`` ersetzt den Vorrang des Inline-Styles; die eigenen Blöcke der
    Mail stehen danach und gewinnen bei gleicher Spezifität wie im Mailprogramm (dunkles Schema)."""
    rules: dict[str, int] = {}

    def attr(m: re.Match) -> str:
        n = rules.setdefault(unescape(m.group(2)), len(rules))
        return f'{m.group(1)} data-ks="{n}"'

    html = _STYLE_TAG.sub(f'<style nonce="{nonce}"', _STYLE_ATTR.sub(attr, html))
    css = "".join(
        f'[data-ks="{n}"]{{{";".join(d.strip() + " !important" for d in decl.split(";") if d.strip())}}}'
        for decl, n in rules.items()).replace("<", "")   # kein </style> aus Attributwerten
    block = f'<style nonce="{nonce}">{css}</style>'
    i = html.find("<style")
    if i < 0:
        i = html.find("</head>")
    return html[:i] + block + html[i:] if i >= 0 else block + html


def send(to: str, template: str, lang: str = "de", /, attachments=None, headers=None, **ctx) -> bool:
    """Mail rendern und mit Logo (CID) senden."""
    r = render(template, lang, **ctx)
    png, _w, _h = logo()
    return mail.send(to, r.subject, r.text, r.html, attachments=attachments, headers=headers,
                     inline=[(LOGO_CID, png, "image/png")], branded=True)


# ---------- Beispieldaten (Vorschau im Verwaltungsbereich, Tests) ----------

def samples() -> dict[str, dict]:
    """Mailtyp -> Beispiel-Variablen. Neuer Mailtyp → hier ergänzen (Vorschau und Tests nutzen die Liste)."""
    from datetime import date, datetime, timedelta
    from types import SimpleNamespace
    from . import report, schedule as sch
    from .i18n import _
    url = settings.base_url
    today = date.today()
    period = report.last_period("weekly", today)
    return {
        "email_verify": dict(name="Mama", link=f"{url}/email/verify/BEISPIEL", hours=48),
        "password_reset": dict(name="Mama", username="mama", link=f"{url}/reset/BEISPIEL", minutes=30),
        "security_notice": dict(name="Mama", title=_("Passwort geändert"),
                                text=_("dein Passwort wurde im Profil geändert.")),
        "invite": dict(by="Mama", link=f"{url}/invite/BEISPIEL", days=7),
        "signup_verify": dict(link=f"{url}/signup/BEISPIEL", hours=24),
        "signup_invite": dict(code="ABCD-EFGH-JKLM", link=f"{url}/signup?code=ABCD-EFGH-JKLM",
                              expires=datetime.combine(today + timedelta(days=30), datetime.min.time()), free=False,
                              trial_days=settings.trial_days),
        "billing_notice": dict(name="Mama", title=_("KidCTL pausiert"),
                               text=_("Beispiel: Euer Abo ist abgelaufen.")),
        "ticket_support": dict(ticket_id=42, family_id=7, new=True, link=f"{url}/admin/tickets/42"),
        "ticket_family": dict(name="Mama", ticket_id=42, closed=False, link=f"{url}/support/42"),
        "invoice": dict(name="Familie Müller", storno=False, number="2026-0001", issue_date=today, gross="59,88 €",
                        due_date=today + timedelta(days=14), pay=True, holder="Beispiel GmbH",
                        iban="DE02 1203 0000 0000 2020 51", bic="BYLADEM1001", sender_name="Beispiel GmbH"),
        "operator_notice": dict(name="betrieb", title=_("Passwort geändert"),
                                text=_("das Passwort deines Betreiber-Zugangs wurde geändert.")),
        "email_changed": dict(username="mama", email="neu@example.org"),
        "passkey_notice": dict(name="Mama", event="added", passkey="iPhone", admin=False),
        "newsletter": _newsletter_sample(f"{url}/newsletter/unsubscribe/1/BEISPIEL"),
        "report": dict(parent=SimpleNamespace(display_name="Mama", username="mama"), period=period, reports=[],
                       fmt=sch.fmt_minutes, short=report._short, bar_height=report.BAR_HEIGHT,
                       intervals=report.INTERVALS),
    }


def _newsletter_sample(unsubscribe_url: str) -> dict:
    from . import mdmail
    body = ("Mit **Version 0.36** kommen zwei Neuerungen:\n\n## Was ist neu?\n\n- Einheitliche Mails\n"
            "- Mehr Spiele unter Windows\n\nAlle Details in den [Neuigkeiten](https://kidctl.de/news/)."
            "\n\n---\n\nViele Grüße")
    return dict(name="Mama", nl_subject="Version 0.36", body=body, body_html=mdmail.to_html(body),
                preheader_text=mdmail.preheader(body), unsubscribe_url=unsubscribe_url)
