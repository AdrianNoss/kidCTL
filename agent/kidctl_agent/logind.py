"""Sitzungen über systemd-logind (D-Bus, System-Bus)."""

from __future__ import annotations

from dataclasses import dataclass

from dbus_next import BusType, Message, MessageType
from dbus_next.aio import MessageBus

LOGIN1 = "org.freedesktop.login1"
MANAGER_PATH = "/org/freedesktop/login1"
MANAGER_IFACE = "org.freedesktop.login1.Manager"
SESSION_IFACE = "org.freedesktop.login1.Session"


@dataclass(frozen=True)
class SessionInfo:
    id: str
    uid: int
    user: str
    session_class: str   # user | greeter | manager | …
    active: bool
    locked: bool
    state: str           # online | active | closing

    @property
    def is_user_session(self) -> bool:
        """Echte Benutzersitzung (kein Greeter, kein user@.service-Manager), nicht im Abbau."""
        return self.session_class == "user" and self.state != "closing"

    @property
    def counts(self) -> bool:
        """Zählt für die Bildschirmzeit: aktiv und entsperrt."""
        return self.is_user_session and self.active and not self.locked


class Logind:
    def __init__(self) -> None:
        self._bus: MessageBus | None = None

    async def _connect(self) -> MessageBus:
        if self._bus is None or not self._bus.connected:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    async def _call(self, path: str, iface: str, member: str, signature: str = "", body=None):
        bus = await self._connect()
        reply = await bus.call(Message(destination=LOGIN1, path=path, interface=iface, member=member,
                                       signature=signature, body=body or []))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name} {reply.body}")
        return reply.body

    async def sessions(self) -> list[SessionInfo]:
        (rows,) = await self._call(MANAGER_PATH, MANAGER_IFACE, "ListSessions")
        out = []
        for sid, uid, user, _seat, obj_path in rows:
            try:
                (props,) = await self._call(obj_path, "org.freedesktop.DBus.Properties", "GetAll",
                                            "s", [SESSION_IFACE])
            except RuntimeError:
                continue  # Sitzung ist zwischenzeitlich verschwunden
            out.append(SessionInfo(
                id=sid, uid=uid, user=user,
                session_class=props["Class"].value, active=props["Active"].value,
                locked=props["LockedHint"].value, state=props["State"].value))
        return out

    async def terminate(self, session_id: str) -> None:
        await self._call(MANAGER_PATH, MANAGER_IFACE, "TerminateSession", "s", [session_id])
