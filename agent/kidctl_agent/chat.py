"""Chat-Verlauf (lokaler Cache je Kind) und Ausgangs-Warteschlange für Offline-Betrieb."""

from __future__ import annotations

import uuid

from pydantic import TypeAdapter

from kidctl_common import protocol as p

from . import store

CHAT_FILE = "chat.json"
OUTBOX_FILE = "outbox.json"
KEEP_MESSAGES = 200  # je Kind

_outbox_item = TypeAdapter(p.ChatSend | p.ExtensionRequest)

NEW, UPDATED = "new", "updated"


class ChatStore:
    def __init__(self) -> None:
        self.by_child: dict[int, dict[int, p.ChatMessage]] = {}

    @classmethod
    def load(cls) -> ChatStore:
        c = cls()
        for m in (store.load(CHAT_FILE) or {}).get("messages", []):
            msg = p.ChatMessage.model_validate(m)
            c.by_child.setdefault(msg.child_id, {})[msg.id] = msg
        return c

    def save(self) -> None:
        store.save(CHAT_FILE, {"messages": [m.model_dump(mode="json")
                                            for msgs in self.by_child.values() for m in msgs.values()]})

    def upsert(self, msg: p.ChatMessage) -> str | None:
        """Gibt ``NEW``, ``UPDATED`` oder ``None`` (unverändert) zurück."""
        msgs = self.by_child.setdefault(msg.child_id, {})
        old = msgs.get(msg.id)
        if old == msg:
            return None
        msgs[msg.id] = msg
        if len(msgs) > KEEP_MESSAGES:
            for mid in sorted(msgs)[:-KEEP_MESSAGES]:
                del msgs[mid]
        return NEW if old is None else UPDATED

    def previous(self, msg: p.ChatMessage) -> p.ChatMessage | None:
        return self.by_child.get(msg.child_id, {}).get(msg.id)

    def history(self, child_id: int) -> list[p.ChatMessage]:
        return [m for _, m in sorted(self.by_child.get(child_id, {}).items())]

    def max_id(self, child_id: int) -> int:
        return max(self.by_child.get(child_id, {}) or [0])

    def has_history(self, child_id: int) -> bool:
        return bool(self.by_child.get(child_id))


class Outbox:
    """Nachrichten/Anfragen des Kindes, bis der Server sie per ``ack`` bestätigt hat."""

    def __init__(self) -> None:
        self.items: dict[str, p.ChatSend | p.ExtensionRequest] = {}

    @classmethod
    def load(cls) -> Outbox:
        o = cls()
        for raw in (store.load(OUTBOX_FILE) or {}).get("items", []):
            item = _outbox_item.validate_python(raw)
            o.items[item.client_msg_id] = item
        return o

    def save(self) -> None:
        store.save(OUTBOX_FILE, {"items": [i.model_dump(mode="json") for i in self.items.values()]})

    def add(self, item: p.ChatSend | p.ExtensionRequest) -> None:
        self.items[item.client_msg_id] = item
        self.save()

    def remove(self, client_msg_id: str) -> None:
        if self.items.pop(client_msg_id, None) is not None:
            self.save()

    def pending_for(self, usernames: list[str]) -> int:
        return sum(1 for i in self.items.values() if i.username in usernames)


def new_client_msg_id() -> str:
    return uuid.uuid4().hex
