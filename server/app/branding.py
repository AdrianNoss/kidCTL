"""Whitelabel: eigener Name, Logo und App-Icon für Eltern-WebUI, PWA und Mails.

Gepflegt im Verwaltungsbereich (Tabelle ``branding``, eine Zeile). Ohne Einstellung heißt alles „KidCTL“. Mit eigenem
Namen steht überall zusätzlich „powered by KidCTL“. Kinder-Clients (Tray, Installer) bleiben immer KidCTL.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

DEFAULT_NAME = "KidCTL"
PROJECT_SITE = "https://adriannoss.github.io/kidCTL/"
POWERED_BY = "powered by KidCTL"
MAX_UPLOAD = 2 * 1024 * 1024          # Bytes je Bild beim Hochladen
LOGO_MAX = (512, 128)                 # Logo wird höchstens so groß gespeichert
ICON_SIZE = 512                       # App-Icon quadratisch
ICON_SIZES = (512, 192, 180, 32)      # ausgelieferte Größen (PWA, Apple, Favicon)
CACHE_SECONDS = 30                    # mehrere Worker: spätestens nach 30 s überall aktuell


@dataclass(frozen=True)
class Brand:
    name: str = DEFAULT_NAME
    custom: bool = False              # eigener Name gesetzt → „powered by KidCTL“ zeigen
    has_logo: bool = False
    has_icon: bool = False
    logo_only: bool = False           # Logo enthält den Namen → Text daneben nur für Screenreader
    version: int = 0                  # für Cache-Busting der Bild-URLs
    homepage_url: str = ""            # Link unter den Anmeldeseiten; leer: Projektseite
    homepage_labels: tuple[str, str] = ("", "")   # Linktext DE, EN

    def homepage(self, lang: str = "de") -> tuple[str, str]:
        """(URL, eigener Linktext oder "") für den Link unter den Anmeldeseiten."""
        if not self.homepage_url:
            return PROJECT_SITE + ("de/" if lang == "de" else ""), ""
        de, en = self.homepage_labels
        return self.homepage_url, (en or de) if lang == "en" else (de or en)

    @property
    def powered_by(self) -> str:
        return POWERED_BY if self.custom else ""

    def logo_url(self) -> str:
        return f"/brand/logo.png?v={self.version}"

    def icon_url(self, size: int = 192) -> str:
        return f"/brand/icon-{size}.png?v={self.version}"


_cached: tuple[float, Brand] | None = None
_images: dict[tuple[str, int, int], bytes] = {}


def current() -> Brand:
    """Aktuelles Branding (kurz zwischengespeichert; Fehler beim Lesen → Standard)."""
    global _cached
    now = time.monotonic()
    if _cached and now - _cached[0] < CACHE_SECONDS:
        return _cached[1]
    brand = Brand()
    try:
        from sqlalchemy import select
        from .models import Branding
        from .tenant import platform_session
        with platform_session() as db:
            row = db.execute(select(Branding.name, Branding.logo_only, Branding.logo.is_not(None),
                                    Branding.icon.is_not(None), Branding.updated_at, Branding.homepage_url,
                                    Branding.homepage_label_de, Branding.homepage_label_en)
                             .where(Branding.id == 1)).first()
        if row:
            name = (row[0] or "").strip()
            brand = Brand(name=name or DEFAULT_NAME, custom=bool(name) and name != DEFAULT_NAME, has_logo=row[2],
                          has_icon=row[3], logo_only=bool(row[1]) and row[2],
                          version=int(row[4].timestamp()) if row[4] else 0, homepage_url=row[5] or "",
                          homepage_labels=(row[6] or "", row[7] or ""))
    except Exception:   # Datenbank (noch) nicht erreichbar – Seiten und Mails trotzdem ausliefern
        return brand
    _cached = (now, brand)
    return brand


def invalidate() -> None:
    global _cached
    _cached = None
    _images.clear()


def name() -> str:
    return current().name


def apply(text: str) -> str:
    """„KidCTL“ in Texten an Familien durch den eigenen Namen ersetzen (nur mit eigenem Namen)."""
    b = current()
    return text.replace(DEFAULT_NAME, b.name) if b.custom and text else text


def mail_parts(subject: str, body: str, html: str | None) -> tuple[str, str, str | None]:
    """Betreff und Text einer Mail an das Branding anpassen; mit eigenem Namen Fußzeile „powered by KidCTL“."""
    b = current()
    if not b.custom:
        return subject, body, html
    subject, body = apply(subject), apply(body).rstrip("\n") + f"\n\n-- \n{b.name} · {POWERED_BY}\n"
    if html:
        html = apply(html)
    return subject, body, html


def totp_issuer() -> str:
    """Name im Authenticator: TOTP_ISSUER aus .env, sonst der (eigene) Name."""
    from .config import settings
    return settings.totp_issuer or name()


# ---------- Bilder ----------

class ImageError(ValueError):
    pass


def _open(data: bytes):
    from PIL import Image, UnidentifiedImageError
    if len(data) > MAX_UPLOAD:
        raise ImageError("too_big")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP", "GIF", "ICO"):
            raise ImageError("format")
        if img.width * img.height > 25_000_000:
            raise ImageError("too_big")
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImageError("format") from e
    return img.convert("RGBA")


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def normalize_logo(data: bytes) -> bytes:
    """Logo als PNG, verkleinert auf höchstens 512×128 (Seitenverhältnis bleibt)."""
    img = _open(data)
    img.thumbnail(LOGO_MAX)
    return _png(img)


def normalize_icon(data: bytes) -> bytes:
    """App-Icon als quadratisches PNG 512×512 (nicht quadratische Bilder mittig mit transparentem Rand)."""
    from PIL import Image
    img = _open(data)
    img.thumbnail((ICON_SIZE, ICON_SIZE))
    square = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    square.paste(img, ((ICON_SIZE - img.width) // 2, (ICON_SIZE - img.height) // 2), img)
    return _png(square)


def image(kind: str, size: int = 0) -> bytes | None:
    """Gespeichertes Logo bzw. Icon in der gewünschten Größe (zwischengespeichert je Version)."""
    b = current()
    key = (kind, size, b.version)
    if key in _images:
        return _images[key]
    from .models import Branding
    from .tenant import platform_session
    with platform_session() as db:
        row = db.get(Branding, 1)
        data = (row.logo if kind == "logo" else row.icon) if row else None
    if data and kind == "icon" and size and size != ICON_SIZE:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        data = _png(img.resize((size, size), Image.LANCZOS))
    if data:
        _images[key] = data
    return data
