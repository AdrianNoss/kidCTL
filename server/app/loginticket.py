"""Login in zwei Schritten: Nach richtigem Passwort ein kurzlebiges Ticket (Cookie), dann der Authenticator-Code.

Das Ticket ist an Konto und Art (Eltern/Betreiber) gebunden, 5 Minuten gültig, einmal verwendbar und nach
5 falschen Codes ungültig. Gespeichert wird nur der Hash des Tokens. Abgelaufene Tickets räumt ``create`` mit weg.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .models import LoginTicket, utcnow
from .security import new_token, token_hash

TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
COOKIES = {"parent": ("kidctl_login", "/"), "operator": ("kidctl_admin_login", "/admin")}


def create(db: Session, kind: str, account_id: int) -> str:
    db.execute(delete(LoginTicket).where(LoginTicket.expires_at < utcnow()))   # Aufräumen
    token = new_token()
    db.add(LoginTicket(token_hash=token_hash(token), kind=kind, account_id=account_id, expires_at=utcnow() + TTL))
    db.commit()
    return token


def get(db: Session, request, kind: str) -> LoginTicket | None:
    """Gültiges Ticket aus dem Cookie (passende Art, nicht abgelaufen, Versuche übrig) oder ``None``."""
    token = request.cookies.get(COOKIES[kind][0])
    if not token:
        return None
    t = db.scalar(select(LoginTicket).where(LoginTicket.token_hash == token_hash(token)))
    if t is None or t.kind != kind or t.expires_at < utcnow() or t.attempts >= MAX_ATTEMPTS:
        return None
    return t


def failed(db: Session, t: LoginTicket) -> bool:
    """Falscher Code: Versuch zählen. True = Ticket jetzt verbraucht (zurück zu Schritt 1)."""
    t.attempts += 1
    spent = t.attempts >= MAX_ATTEMPTS
    if spent:
        db.delete(t)
    db.commit()
    return spent


def consume(db: Session, t: LoginTicket) -> None:
    db.delete(t)
    db.commit()


def set_cookie(response, kind: str, token: str) -> None:
    name, path = COOKIES[kind]
    response.set_cookie(name, token, max_age=int(TTL.total_seconds()), httponly=True, secure=settings.cookie_secure,
                        samesite="strict", path=path)


def clear_cookie(response, kind: str) -> None:
    name, path = COOKIES[kind]
    response.delete_cookie(name, path=path)
