"""Sitzung eines Benutzers, plattformneutral (Linux: logind, Windows: WTS)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionInfo:
    id: str
    uid: int
    user: str
    session_class: str   # user | greeter | manager | …
    active: bool
    locked: bool
    state: str           # online | active | closing
    type: str = "unspecified"  # wayland | x11 | tty | unspecified | windows
    seat: str = ""
    desktop: str = ""    # XDG_SESSION_DESKTOP, z. B. KDE, gnome (GDM trägt ihn nicht ein)
    leader: int = 0      # PID des Sitzungsleiters (sddm-helper, gdm-session-worker, login …); Windows: Anmeldezeit

    @property
    def graphical(self) -> bool:
        return self.type in ("wayland", "x11", "windows")

    @property
    def is_user_session(self) -> bool:
        """Echte Benutzersitzung (kein Greeter, kein user@.service-Manager), nicht im Abbau."""
        return self.session_class == "user" and self.state != "closing"

    @property
    def counts(self) -> bool:
        """Zählt für die Bildschirmzeit: aktiv und entsperrt."""
        return self.is_user_session and self.active and not self.locked
