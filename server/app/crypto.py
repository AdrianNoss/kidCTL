"""Verschlüsselung je Familie (Etappe 2 Multi-Tenant).

- **Hauptschlüssel** ``KIDCTL_MASTER_KEY`` (32 Byte, Base64) aus der Umgebung. Daraus per HKDF: ein Schlüssel zum
  Einpacken der Familienschlüssel und einer für Suchwerte (E-Mail). Geht er verloren, sind alle verschlüsselten Daten
  verloren – getrennt sichern.
- **Datenschlüssel je Familie** (64 Byte, zufällig), mit dem Hauptschlüssel verschlüsselt in ``family.wrapped_key``.
  Familie löschen = Schlüssel verwerfen: ihre Daten sind dann auch in alten Backups unlesbar.
- **Feldtypen:** ``Sealed`` (AES-256-GCM, zufällige Nonce – Freitexte) und ``Matchable`` (AES-256-SIV,
  deterministisch – gleicher Klartext ergibt innerhalb einer Familie denselben Geheimtext, damit Eindeutigkeit,
  Gruppieren und Suche nach Gleichheit in der Datenbank funktionieren). Zusätzliche Prüfdaten: Familie, Tabelle,
  Spalte.
- **Kontextbindung:** Ver- und entschlüsselt wird immer mit dem Schlüssel der Familie des aktuellen Kontexts
  (``app.tenant`` setzt ihn bei jeder Abfrage und jedem Speichern), nie mit dem der gelesenen Zeile. Eine fremde Zeile
  führt damit zu ``DecryptError`` statt zur Anzeige fremder Daten; ohne Familienkontext gibt es keinen Schlüssel.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import LargeBinary, Text
from sqlalchemy.types import TypeDecorator

from .config import settings

SEALED, MATCHABLE, PLATFORM = "g1:", "s1:", "p1:"
_current: ContextVar[int | None] = ContextVar("kidctl_crypto_family", default=None)
_keys: dict[int, bytes] = {}   # entpackte Familienschlüssel (nur im Arbeitsspeicher)


class CryptoError(RuntimeError):
    """Verschlüsselung ohne Familienkontext bzw. ohne Schlüssel."""


class DecryptError(CryptoError):
    """Geheimtext passt nicht zum Schlüssel/Kontext (fremde Familie, falsche Spalte, beschädigt)."""


# ---------- Schlüssel ----------

def _master() -> bytes:
    raw = settings.master_key
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except ValueError:
        key = b""
    if len(key) != 32:
        raise CryptoError("KIDCTL_MASTER_KEY fehlt oder ist ungültig (32 Byte, Base64) – erzeugen mit "
                          "`python -m app.cli master-key`.")
    return key


_derived: dict[tuple[bytes, str], bytes] = {}


def _derive(info: bytes) -> bytes:
    """Abgeleiteter Schlüssel (zwischengespeichert je Hauptschlüssel – HKDF bei jedem Feld wäre unnötig teuer)."""
    cache_key = (info, settings.master_key)
    key = _derived.get(cache_key)
    if key is None:
        key = _derived[cache_key] = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(_master())
    return key


def new_master_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def wrap_family_key(family_id: int, dek: bytes | None = None) -> bytes:
    """Neuen (oder gegebenen) Familienschlüssel erzeugen und mit dem Hauptschlüssel einpacken."""
    dek = dek or os.urandom(64)
    nonce = os.urandom(12)
    return nonce + AESGCM(_derive(b"kidctl family-key wrap")).encrypt(nonce, dek, f"family:{family_id}".encode())


def unwrap_family_key(family_id: int, wrapped: bytes) -> bytes:
    try:
        return AESGCM(_derive(b"kidctl family-key wrap")).decrypt(wrapped[:12], wrapped[12:],
                                                                  f"family:{family_id}".encode())
    except InvalidTag as e:
        raise CryptoError(f"Schlüssel der Familie {family_id} passt nicht zum Hauptschlüssel") from e


def remember(family_id: int, dek: bytes) -> None:
    _keys[family_id] = dek


def forget(family_id: int) -> None:
    _keys.pop(family_id, None)


def _key(family_id: int) -> bytes:
    key = _keys.get(family_id)
    if key is None:
        from .db import engine   # Schlüssel nachladen (Plattform-Lesezugriff ohne ORM-Ereignisse)
        from sqlalchemy import text
        with engine.connect() as conn:
            wrapped = conn.execute(text("SELECT wrapped_key FROM family WHERE id = :id"), {"id": family_id}).scalar()
        if not wrapped:
            raise CryptoError(f"Familie {family_id} hat keinen Schlüssel (gelöscht?)")
        key = _keys[family_id] = unwrap_family_key(family_id, bytes(wrapped))
    return key


def email_index(email: str) -> str:
    """Suchwert für die Anmeldung per E-Mail (ohne Groß/klein), über alle Familien eindeutig."""
    if not email:
        return ""
    return hmac.new(_derive(b"kidctl email index"), email.strip().lower().encode(), hashlib.sha256).hexdigest()


# ---------- Kontext ----------

def set_current(family_id: int | None) -> None:
    _current.set(family_id)


@contextmanager
def family(family_id: int) -> Iterator[None]:
    """Für Zugriffe ohne Session-Ereignisse (Core-Insert bei der Übernahme, Migration)."""
    token = _current.set(family_id)
    try:
        yield
    finally:
        _current.reset(token)


def _need_family(label: str) -> int:
    fid = _current.get()
    if fid is None:
        raise CryptoError(f"{label}: Ver-/Entschlüsselung ohne Familienkontext")
    return fid


def _aad(fid: int, label: str) -> bytes:
    return f"{fid}|{label}".encode()


def seal(value: str, label: str, family_id: int | None = None) -> str:
    fid = family_id if family_id is not None else _need_family(label)
    nonce = os.urandom(12)
    ct = AESGCM(_key(fid)[:32]).encrypt(nonce, value.encode(), _aad(fid, label))
    return SEALED + base64.b64encode(nonce + ct).decode()


def match(value: str, label: str, family_id: int | None = None) -> str:
    fid = family_id if family_id is not None else _need_family(label)
    return MATCHABLE + base64.b64encode(AESSIV(_key(fid)).encrypt(value.encode(), [_aad(fid, label)])).decode()


def open_(value: str, label: str, family_id: int | None = None) -> str:
    fid = family_id if family_id is not None else _need_family(label)
    try:
        if value.startswith(SEALED):
            raw = base64.b64decode(value[len(SEALED):])
            return AESGCM(_key(fid)[:32]).decrypt(raw[:12], raw[12:], _aad(fid, label)).decode()
        if value.startswith(MATCHABLE):
            return AESSIV(_key(fid)).decrypt(base64.b64decode(value[len(MATCHABLE):]), [_aad(fid, label)]).decode()
    except (InvalidTag, ValueError) as e:
        raise DecryptError(f"{label}: Entschlüsselung fehlgeschlagen (fremde Familie oder beschädigt)") from e
    raise DecryptError(f"{label}: Wert ist nicht verschlüsselt")


# ---------- Spaltentypen ----------

class _Encrypted(TypeDecorator):
    impl = Text
    cache_ok = True
    _encrypt = staticmethod(seal)

    def __init__(self, label: str):
        super().__init__()
        self.label = label

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return self._encrypt(str(value), self.label)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return open_(value, self.label)


class Sealed(_Encrypted):
    """Freitext, zufällig verschlüsselt (AES-GCM). Nicht durchsuchbar, nicht sortierbar."""
    cache_ok = True   # Ver-/Entschlüsselung erst beim Ausführen (Kontext), nicht beim Kompilieren
    _encrypt = staticmethod(seal)


class Matchable(_Encrypted):
    """Deterministisch verschlüsselt (AES-SIV): Gleichheit, Eindeutigkeit und Gruppieren in der Datenbank."""
    cache_ok = True   # Ver-/Entschlüsselung erst beim Ausführen (Kontext), nicht beim Kompilieren
    _encrypt = staticmethod(match)


# ---------- Plattform-Daten (Betreiber muss sie lesen können) ----------

def seal_platform(value: str, label: str) -> str:
    """Daten, die der Betreiber lesen muss (Familienname, Rechnungsanschrift): eigener, vom Hauptschlüssel
    abgeleiteter Schlüssel – lesbar ohne Familienkontext, aber nicht in einer Kopie der Datenbank allein."""
    nonce = os.urandom(12)
    ct = AESGCM(_derive(b"kidctl platform data")).encrypt(nonce, value.encode(), label.encode())
    return PLATFORM + base64.b64encode(nonce + ct).decode()


def open_platform(value: str, label: str) -> str:
    if value == "":
        return ""
    if not value.startswith(PLATFORM):
        raise DecryptError(f"{label}: Wert ist nicht verschlüsselt")
    raw = base64.b64decode(value[len(PLATFORM):])
    try:
        return AESGCM(_derive(b"kidctl platform data")).decrypt(raw[:12], raw[12:], label.encode()).decode()
    except InvalidTag as e:
        raise DecryptError(f"{label}: Entschlüsselung fehlgeschlagen") from e


class PlatformSealed(TypeDecorator):
    """Mit dem Plattform-Schlüssel verschlüsselt (AES-GCM) – unabhängig vom Familienkontext."""
    impl = Text
    cache_ok = True

    def __init__(self, label: str):
        super().__init__()
        self.label = label

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return seal_platform(str(value), self.label)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return open_platform(value, self.label)


WrappedKey = LargeBinary
