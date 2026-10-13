"""Lokales Protokoll Tray-App <-> Agent (Unix-Socket, eine JSON-Nachricht pro Zeile).

Der Agent bestimmt den User ausschließlich über ``SO_PEERCRED`` (UID des verbundenen
Prozesses) – die Tray-App kann nicht im Namen eines anderen Users sprechen. Nicht zugeordnete
User erhalten ``state`` mit ``restricted=False`` und sonst nichts.

Ablauf: Tray sendet ``hello``; Agent antwortet mit ``state`` und ``chat`` (Verlauf) und schickt
danach von sich aus ``state`` (bei Änderung, spätestens alle 30 s), ``chat_message`` (neu/
geändert) und ``open_request_dialog`` (Klick auf „Verlängerung anfordern“ in der Warnung).
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from .protocol import MAX_BODY, ChatMessage

TRAY_PROTOCOL_VERSION = 1
SOCKET_PATH = "/run/kidctl/tray.sock"


# ---------- Tray -> Agent ----------

class TrayHello(BaseModel):
    type: Literal["hello"] = "hello"
    version: int = TRAY_PROTOCOL_VERSION


class TrayChatSend(BaseModel):
    type: Literal["chat_send"] = "chat_send"
    body: str = Field(min_length=1, max_length=MAX_BODY)


class TrayExtensionRequest(BaseModel):
    """Verlängerung für heute: mehr Minuten und/oder späteres Fensterende, Begründung Pflicht."""
    type: Literal["extension_request"] = "extension_request"
    minutes: int = Field(default=0, ge=0, le=720)
    window_end: time | None = None
    reason: str = Field(min_length=1, max_length=MAX_BODY)


# ---------- Agent -> Tray ----------

class TrayState(BaseModel):
    type: Literal["state"] = "state"
    restricted: bool                 # False: User ist keinem Kind zugeordnet
    child_name: str = ""
    allowed: bool = True
    remaining_seconds: int = 0
    reason: str | None = None        # quota | window_not_open | window_closed
    used_seconds: int = 0
    allowed_minutes: int = 0         # Kontingent heute inkl. Gutschrift
    window_start: time | None = None
    window_end: time | None = None
    online: bool = False             # Agent mit Server verbunden
    pending_outbox: int = 0          # noch nicht zugestellte Nachrichten/Anfragen


class TrayChat(BaseModel):
    """Vollständiger Verlauf (nach ``hello``)."""
    type: Literal["chat"] = "chat"
    messages: list[ChatMessage]


class TrayChatMessage(BaseModel):
    type: Literal["chat_message"] = "chat_message"
    message: ChatMessage


class TrayQueued(BaseModel):
    """Bestätigung: Nachricht/Anfrage liegt in der Warteschlange des Agenten."""
    type: Literal["queued"] = "queued"
    kind: Literal["chat_send", "extension_request"]
    online: bool


class TrayOpenRequestDialog(BaseModel):
    type: Literal["open_request_dialog"] = "open_request_dialog"


class TrayError(BaseModel):
    type: Literal["error"] = "error"
    message: str


TrayToAgent = Annotated[Union[TrayHello, TrayChatSend, TrayExtensionRequest],
                        Field(discriminator="type")]
AgentToTray = Annotated[Union[TrayState, TrayChat, TrayChatMessage, TrayQueued,
                              TrayOpenRequestDialog, TrayError], Field(discriminator="type")]

tray_to_agent = TypeAdapter(TrayToAgent)
agent_to_tray = TypeAdapter(AgentToTray)
