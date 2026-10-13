"""WebSocket-Protokoll Agent <-> Server.

Jede Nachricht ist ein JSON-Objekt mit Feld ``type``. Der Agent sendet als erste Nachricht
``hello`` mit seiner Protokollversion; der Server antwortet mit ``welcome`` oder ``error``.

Versionierung: ``PROTOCOL_VERSION`` ist die aktuelle Version. Der Server akzeptiert alle
Versionen in ``SUPPORTED_VERSIONS`` (Ziel: aktuelle + mindestens eine ältere).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1
# Bei Version 2 hier {1, 2} eintragen – mindestens eine ältere Version bleibt unterstützt.
SUPPORTED_VERSIONS = frozenset({1})

WS_PATH = "/api/v1/ws"
ENROLL_PATH = "/api/v1/enroll"

# WebSocket-Close-Codes (4000–4999 anwendungsspezifisch)
CLOSE_UNAUTHORIZED = 4401
CLOSE_REVOKED = 4403
CLOSE_UNSUPPORTED_VERSION = 4426
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_REPLACED = 4409


# ---------- Kopplung (HTTP) ----------

class EnrollRequest(BaseModel):
    code: str = Field(min_length=8, max_length=16)
    hostname: str = Field(min_length=1, max_length=255)


class EnrollResponse(BaseModel):
    device_id: int
    token: str
    protocol_version: int = PROTOCOL_VERSION


# ---------- WebSocket-Nachrichten ----------

class Hello(BaseModel):
    """Agent -> Server, erste Nachricht nach dem Verbindungsaufbau."""
    type: Literal["hello"] = "hello"
    protocol_version: int
    agent_version: str = Field(max_length=64)
    hostname: str = Field(max_length=255)


class Welcome(BaseModel):
    """Server -> Agent, Antwort auf ``hello``."""
    type: Literal["welcome"] = "welcome"
    protocol_version: int
    device_id: int
    server_time: str  # ISO 8601, UTC


class Error(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str = ""


class Ping(BaseModel):
    type: Literal["ping"] = "ping"


class Pong(BaseModel):
    type: Literal["pong"] = "pong"


class LocalUser(BaseModel):
    username: str = Field(max_length=64)
    uid: int = Field(ge=1000)
    full_name: str = Field(default="", max_length=255)


class ReportUsers(BaseModel):
    """Agent -> Server: lokale Benutzer mit UID >= 1000."""
    type: Literal["report_users"] = "report_users"
    users: list[LocalUser] = Field(max_length=500)


# ---------- Chat & Verlängerungen ----------
# Der Agent leitet Nachrichten der Tray-App weiter und puffert sie offline. ``client_msg_id``
# macht das Senden idempotent: Der Server bestätigt jede Nachricht mit ``ack`` (auch Duplikate),
# erst danach entfernt der Agent sie aus seiner Warteschlange.

MAX_BODY = 2000


class ChatSend(BaseModel):
    """Agent -> Server: Textnachricht eines Kindes (``username`` = Linux-User)."""
    type: Literal["chat_send"] = "chat_send"
    client_msg_id: str = Field(min_length=8, max_length=64)
    username: str = Field(max_length=64)
    body: str = Field(min_length=1, max_length=MAX_BODY)


class ExtensionRequest(BaseModel):
    """Agent -> Server: Verlängerungsanfrage (mehr Minuten und/oder späteres Fensterende)."""
    type: Literal["extension_request"] = "extension_request"
    client_msg_id: str = Field(min_length=8, max_length=64)
    username: str = Field(max_length=64)
    day: date  # lokaler Kalendertag, für den die Verlängerung gelten soll
    minutes: int = Field(default=0, ge=0, le=720)
    window_end: time | None = None
    reason: str = Field(min_length=1, max_length=MAX_BODY)


class ChatSync(BaseModel):
    """Agent -> Server: Verlauf ab ``after_id`` für einen Linux-User anfordern."""
    type: Literal["chat_sync"] = "chat_sync"
    username: str = Field(max_length=64)
    after_id: int = Field(default=0, ge=0)


class Ack(BaseModel):
    """Server -> Agent: Bestätigung für ``chat_send``/``extension_request``.

    ``ok=False`` mit ``error`` = endgültig abgelehnt (z. B. User keinem Kind zugeordnet);
    der Agent verwirft die Nachricht dann ebenfalls.
    """
    type: Literal["ack"] = "ack"
    client_msg_id: str
    ok: bool
    message_id: int | None = None
    error: str = ""


class ChatMessage(BaseModel):
    """Server -> Agent: Chat-Eintrag (neu oder aktualisiert, z. B. Entscheidung einer Anfrage).

    Der Agent ersetzt Einträge mit gleicher ``id`` (Upsert).
    """
    type: Literal["chat_message"] = "chat_message"
    id: int
    child_id: int
    from_child: bool
    sender_name: str
    kind: Literal["text", "extension_request"]
    body: str
    created_at: datetime  # UTC
    request_day: date | None = None
    requested_minutes: int | None = None
    requested_window_end: time | None = None
    status: Literal["pending", "approved", "rejected", "expired"] | None = None
    approved_minutes: int | None = None
    approved_window_end: time | None = None


AgentMessage = Annotated[
    Union[Hello, Ping, Pong, ReportUsers, ChatSend, ExtensionRequest, ChatSync],
    Field(discriminator="type")]
ServerMessage = Annotated[
    Union[Welcome, Error, Ping, Pong, Ack, ChatMessage], Field(discriminator="type")]

agent_message_adapter: TypeAdapter[AgentMessage] = TypeAdapter(AgentMessage)
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
