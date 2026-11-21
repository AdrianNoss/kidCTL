"""Schutz für jede Anfrage: Größenlimit (ohne Anmeldung erreichbar), Content-Security-Policy und Schutz-Header.

Als reine ASGI-Middleware, damit das Limit greift, bevor irgendetwas den Body in den Speicher liest. Weder uvicorn
noch Traefik begrenzen ihn von sich aus.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse

# Größtes echtes Formular: Programmliste eines Kindes (einige Tausend Kästchen)
MAX_BODY_BYTES = 2 * 1024 * 1024
# Skripte und Styles nur als Dateien von hier – keine Inline-Styles (Templates ohne style-Attribut, Test)
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'{nonce}; img-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
CSP = _CSP.format(nonce="")
# Weitere Schutz-Header (Web-Scan, Issue #3). Passkeys (publickey-credentials-*) bleiben erlaubt.
HEADERS = {
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
}


def csp(style_nonce: str = "") -> str:
    """CSP; mit ``style_nonce`` zusätzlich <style>-Blöcke mit dieser Nonce (Mail-Vorschauen, ``mailer.csp_safe``)."""
    return _CSP.format(nonce=f" 'nonce-{style_nonce}'" if style_nonce else "")


class SecurityMiddleware:
    def __init__(self, app, max_body: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Statische Dateien mit Prüfsumme in der URL (web.asset) ändern sich nie: lange zwischenspeichern
        versioned = scope["path"].startswith("/static/") and b"v=" in scope.get("query_string", b"")

        async def send_with_csp(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", CSP)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
                if versioned and message["status"] == 200:
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                elif not scope["path"].startswith(("/static/", "/repo/")):
                    headers.setdefault("Cache-Control", "no-store")   # Seiten mit Familiendaten nie zwischenspeichern
            await send(message)

        declared = Headers(scope=scope).get("content-length", "")
        if declared and (not declared.isdigit() or int(declared) > self.max_body):
            await PlainTextResponse("Anfrage zu groß", status_code=413)(scope, receive, send_with_csp)
            return

        seen = 0

        async def limited_receive():   # ohne oder mit falscher Längenangabe (chunked)
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_body:
                    raise HTTPException(413, "Anfrage zu groß")
            return message

        await self.app(scope, limited_receive, send_with_csp)
