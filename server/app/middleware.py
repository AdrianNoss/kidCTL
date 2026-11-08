"""Schutz für jede Anfrage: Größenlimit (ohne Anmeldung erreichbar) und Content-Security-Policy.

Als reine ASGI-Middleware, damit das Limit greift, bevor irgendetwas den Body in den Speicher liest. Weder uvicorn
noch Traefik begrenzen ihn von sich aus.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse

# Größtes echtes Formular: Programmliste eines Kindes (einige Tausend Kästchen)
MAX_BODY_BYTES = 2 * 1024 * 1024
# Inline-Styles braucht die Oberfläche (Balkenhöhen), Skripte kommen nur als Dateien von hier
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


class SecurityMiddleware:
    def __init__(self, app, max_body: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_csp(message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).setdefault("Content-Security-Policy", CSP)
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
