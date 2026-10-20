"""Lokales Protokoll Tray-App <-> Agent (Unix-Socket, eine JSON-Nachricht pro Zeile).

Der Agent bestimmt den User ausschließlich über ``SO_PEERCRED`` (UID des verbundenen
Prozesses) – die Tray-App kann nicht im Namen eines anderen Users sprechen. Nicht zugeordnete
User erhalten ``state`` mit ``restricted=False`` und sonst nichts.

Ablauf: Tray sendet ``hello``; Agent antwortet mit ``state`` und ``chat`` (Verlauf) und schickt
danach von sich aus ``state`` (bei Änderung, spätestens alle 30 s), ``chat_message`` (neu/
geändert) und ``open_request_dialog`` (Klick auf „Verlängerung anfordern“ in der Warnung) bzw.
``open_access_dialog`` (Klick auf „Freigabe anfragen“ bei einer Sperre). ``get_blocked`` liefert die
kürzlich gesperrten Programme/Websites (``blocked``), ``access_request`` fragt eine Freigabe an.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from .protocol import MAX_BODY, AccessKind, ChatMessage

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


class TrayGetBlocked(BaseModel):
    """Liste kürzlich gesperrter Programme/Websites anfordern (Antwort: ``blocked``)."""
    type: Literal["get_blocked"] = "get_blocked"


class TrayAccessRequest(BaseModel):
    """Freigabe für einen Eintrag der Sperrliste anfragen; Grund optional."""
    type: Literal["access_request"] = "access_request"
    key: str = Field(max_length=512)
    reason: str = Field(default="", max_length=MAX_BODY)


# ---------- Agent -> Tray ----------

class TrayState(BaseModel):
    type: Literal["state"] = "state"
    restricted: bool                 # False: User ist keinem Kind zugeordnet
    child_name: str = ""
    allowed: bool = True
    remaining_seconds: int = 0
    reason: str | None = None        # quota | window_not_open | window_closed | locked
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
    kind: Literal["chat_send", "extension_request", "access_request"]
    online: bool


class TrayOpenRequestDialog(BaseModel):
    type: Literal["open_request_dialog"] = "open_request_dialog"


class BlockedItem(BaseModel):
    """Kürzlich gesperrtes Programm bzw. Website (Websites zusammengefasst je Hauptdomain)."""
    access_kind: AccessKind
    key: str
    name: str
    last_at: datetime          # UTC
    count: int = 1
    pending: bool = False      # schon angefragt, Antwort steht aus


class TrayBlocked(BaseModel):
    type: Literal["blocked"] = "blocked"
    items: list[BlockedItem]


class TrayOpenAccessDialog(BaseModel):
    """Klick auf „Freigabe anfragen“ in der Sperr-Benachrichtigung."""
    type: Literal["open_access_dialog"] = "open_access_dialog"
    item: BlockedItem


class TrayError(BaseModel):
    type: Literal["error"] = "error"
    message: str


TrayToAgent = Annotated[Union[TrayHello, TrayChatSend, TrayExtensionRequest, TrayGetBlocked, TrayAccessRequest],
                        Field(discriminator="type")]
AgentToTray = Annotated[Union[TrayState, TrayChat, TrayChatMessage, TrayQueued, TrayOpenRequestDialog,
                              TrayBlocked, TrayOpenAccessDialog, TrayError], Field(discriminator="type")]

tray_to_agent = TypeAdapter(TrayToAgent)
agent_to_tray = TypeAdapter(AgentToTray)
