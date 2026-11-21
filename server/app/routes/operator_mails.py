"""Verwaltungsbereich: Mail-Vorschau und Newsletter."""

from __future__ import annotations

import secrets

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import operators as opr
from .. import audit
from ..db import get_platform_db
from ..i18n import N_, _
from ..middleware import csp
from ..models import OperatorSession
from .operator_base import new_router, render as _render

router = new_router()


# ---------- Mail-Vorschau ----------

@router.get("/mail-preview")
def mail_preview(request: Request, type: str = "", lang: str = "de", sess: OperatorSession = Depends(opr.require_admin)):
    """Alle Mailtypen mit Beispieldaten, DE/EN umschaltbar; Ansicht im Rahmen, Testmail an die eigene Adresse."""
    from .. import mailer
    types = sorted(mailer.samples())
    current = type if type in types else types[0]
    lang = lang if lang in ("de", "en") else "de"
    from ..i18n import use
    with use(lang):
        rendered = mailer.render(current, lang, **mailer.samples()[current])
    # Der Rahmen (srcdoc) erbt die CSP der Seite: Inline-Styles der Mail als <style> mit Nonce
    nonce = secrets.token_urlsafe(16)
    html = mailer.csp_safe(mailer.preview_html(rendered.html), nonce)
    resp = _render(request, "admin_mail_preview.html", sess, types=types, current=current, mail_lang=lang,
                   subject=rendered.subject, text=rendered.text, html=html, size=len(rendered.html.encode()),
                   sent=request.query_params.get("sent"))
    resp.headers["Content-Security-Policy"] = csp(nonce)
    return resp


@router.post("/mail-preview/send")
def mail_preview_send(request: Request, type: str = Form(""), lang: str = Form("de"),
                      sess: OperatorSession = Depends(opr.admin_csrf)):
    from urllib.parse import urlencode
    from .. import mailer
    samples = mailer.samples()
    if type not in samples:
        raise HTTPException(404)
    lang = lang if lang in ("de", "en") else "de"
    if not sess.operator.email:
        return RedirectResponse("/admin/mail-preview?" + urlencode({"type": type, "lang": lang, "sent": "noaddr"}),
                                status_code=303)
    from ..i18n import use
    with use(lang):
        ctx = mailer.samples()[type]
    ok = mailer.send(sess.operator.email, type, lang, **ctx)
    return RedirectResponse("/admin/mail-preview?" + urlencode({"type": type, "lang": lang, "sent": "1" if ok else "0"}),
                            status_code=303)


# ---------- Newsletter ----------

NL_FIELDS = {"subject_de": 200, "body_de": 20000, "subject_en": 200, "body_en": 20000}
NL_STATUSES = {"draft": N_("Entwurf"), "sending": N_("wird gesendet"), "sent": N_("gesendet")}


def _nl_or_404(db: Session, newsletter_id: int):
    from ..models import Newsletter
    nl = db.get(Newsletter, newsletter_id)
    if nl is None:
        raise HTTPException(404)
    return nl


def _nl_page(request, db, sess, nl, status_code: int = 200, **ctx):
    from .. import mailer, newsletter
    families = len(newsletter.subscribed_families(db))
    previews = []   # HTML-Vorschau je Sprache
    for lang in ("de", "en") if nl.subject_en and nl.body_en else ("de",):
        if nl.subject_de or lang == "en":
            use, mctx = newsletter.mail_ctx(nl, lang, sess.operator.username, 0)
            html = mailer.render("newsletter", use, **mctx).html
            previews.append((lang, mailer.preview_html(html)))
    ctx.setdefault("previews", previews)
    return _render(request, "admin_newsletter.html", sess, status_code, nl=nl, families=families,
                   statuses=NL_STATUSES, saved=request.query_params.get("saved"), **ctx)


@router.get("/newsletter")
def newsletter_list(request: Request, sess: OperatorSession = Depends(opr.require_admin),
                    db: Session = Depends(get_platform_db)):
    from .. import newsletter
    from ..models import Newsletter
    items = db.scalars(select(Newsletter).order_by(Newsletter.status != "draft", Newsletter.created_at.desc())).all()
    return _render(request, "admin_newsletters.html", sess, items=items, statuses=NL_STATUSES,
                   families=len(newsletter.subscribed_families(db)))


@router.post("/newsletter")
def newsletter_create(sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    from ..models import Newsletter
    nl = Newsletter(created_by=sess.operator.username)
    db.add(nl)
    db.commit()
    return RedirectResponse(f"/admin/newsletter/{nl.id}", status_code=303)


@router.get("/newsletter/{newsletter_id}")
def newsletter_view(newsletter_id: int, request: Request, sess: OperatorSession = Depends(opr.require_admin),
                    db: Session = Depends(get_platform_db)):
    return _nl_page(request, db, sess, _nl_or_404(db, newsletter_id))


@router.post("/newsletter/{newsletter_id}")
async def newsletter_save(newsletter_id: int, request: Request, sess: OperatorSession = Depends(opr.admin_csrf),
                          db: Session = Depends(get_platform_db)):
    """Entwurf speichern; je nach Knopf danach Testversand an die eigene Adresse."""
    from .. import newsletter
    nl = _nl_or_404(db, newsletter_id)
    if nl.status != "draft":
        raise HTTPException(409, "Bereits versendet")
    form = await request.form()
    for field, n in NL_FIELDS.items():
        setattr(nl, field, str(form.get(field, "")).replace("\r\n", "\n").strip()[:n])
    db.commit()
    if form.get("action") == "test":
        if not sess.operator.email:
            return _nl_page(request, db, sess, nl, 400,
                            error=_("Für den Testversand bitte im Profil eine E-Mail-Adresse hinterlegen."))
        if not (nl.subject_de and nl.body_de):
            return _nl_page(request, db, sess, nl, 400, error=_("Bitte mindestens Betreff und Text auf Deutsch angeben."))
        newsletter.send_test(nl, sess.operator.email)
        return RedirectResponse(f"/admin/newsletter/{nl.id}?saved=test", status_code=303)
    return RedirectResponse(f"/admin/newsletter/{nl.id}?saved=1", status_code=303)


@router.post("/newsletter/{newsletter_id}/send")
def newsletter_send(newsletter_id: int, request: Request, background: BackgroundTasks,
                    sess: OperatorSession = Depends(opr.admin_csrf), db: Session = Depends(get_platform_db)):
    """An alle Familien mit Zustimmung senden (im Hintergrund); danach unveränderlich, im Archiv."""
    from sqlalchemy import update
    from .. import newsletter
    from ..models import Newsletter
    nl = _nl_or_404(db, newsletter_id)
    if not (nl.subject_de and nl.body_de):
        return _nl_page(request, db, sess, nl, 400, error=_("Bitte mindestens Betreff und Text auf Deutsch angeben."))
    if bool(nl.subject_en) != bool(nl.body_en):
        return _nl_page(request, db, sess, nl, 400, error=_("Englisch bitte mit Betreff und Text – oder beides leer lassen."))
    # nur einmal: Statuswechsel atomar (Doppelklick, zweiter Admin)
    done = db.execute(update(Newsletter).where(Newsletter.id == nl.id, Newsletter.status == "draft")
                      .values(status="sending", sent_by=sess.operator.username)).rowcount
    if not done:
        raise HTTPException(409, "Bereits versendet")
    families = len(newsletter.subscribed_families(db))
    audit.record(db, audit.actor_of(sess), "newsletter", "sent", None,
                 f"{nl.subject_de} ({families} {_('Familien')})", object_id=nl.id)
    db.commit()
    background.add_task(newsletter.deliver, nl.id)
    return RedirectResponse(f"/admin/newsletter/{nl.id}?saved=sending", status_code=303)


@router.post("/newsletter/{newsletter_id}/delete")
def newsletter_delete(newsletter_id: int, sess: OperatorSession = Depends(opr.admin_csrf),
                      db: Session = Depends(get_platform_db)):
    nl = _nl_or_404(db, newsletter_id)
    if nl.status != "draft":
        raise HTTPException(409, "Versendete Newsletter bleiben im Archiv")
    db.delete(nl)
    db.commit()
    return RedirectResponse("/admin/newsletter", status_code=303)
