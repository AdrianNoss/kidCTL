"""Update des Agenten auf Knopfdruck (Eltern-WebUI).

Der Server sendet nur ``update_now`` – ohne Paket, Quelle oder Befehl; was installiert wird, legt der Agent selbst
fest (eigenes Paket aus der fest eingerichteten, signierten Quelle). Erfolg zeigt sich an der neuen Version im
nächsten ``hello`` (das Paket-Update startet den Agenten neu).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from kidctl_common import protocol as p

from . import versions
from .models import Device, utcnow

DEBOUNCE = timedelta(minutes=10)   # erneutes Auslösen frühestens danach (außer nach Fehler/„aktuell“)
TIMEOUT = timedelta(minutes=15)    # „läuft“ ohne neue Version -> Zeitüberschreitung


def supported(dev: Device) -> bool:
    return p.FEATURE_SELF_UPDATE in (dev.features or "").split(",")


def available(dev: Device) -> str | None:
    """Neuere Version in der Quelle des Geräts (Pacman/APT bzw. Windows-Installer)."""
    return versions.update_for(dev.agent_version, platform=dev.platform)


def state(dev: Device, now: datetime | None = None) -> str:
    """Anzeigestand: ``''``, ``running``, ``timeout``, ``failed``, ``current`` oder ``done``."""
    if dev.update_state == "running" and dev.update_requested_at is not None \
            and (now or utcnow()) - dev.update_requested_at > TIMEOUT:
        return "timeout"
    return dev.update_state


def can_request(dev: Device, online: bool, now: datetime | None = None) -> bool:
    """Knopf aktiv: Gerät aktiv und online, Agent kann es, Update verfügbar, nicht gerade angestoßen."""
    if dev.revoked_at is not None or dev.protocol_unsupported or not online or not supported(dev) \
            or not available(dev):
        return False
    return not (state(dev, now) == "running" and dev.update_requested_at is not None
                and (now or utcnow()) - dev.update_requested_at < DEBOUNCE)


def mark_requested(dev: Device) -> None:
    dev.update_requested_at = utcnow()
    dev.update_from = dev.agent_version
    dev.update_state = "running"
    dev.update_detail = None


def on_hello(dev: Device, hello: p.Hello) -> None:
    """Fähigkeiten übernehmen; neue Version nach angestoßenem Update -> erledigt."""
    dev.features = ",".join(sorted(set(hello.features)))[:255]
    if dev.update_state in ("running", "failed") and dev.update_from and hello.agent_version != dev.update_from:
        dev.update_state = "done"
        dev.update_detail = None


def on_status(dev: Device, msg: p.UpdateStatus) -> None:
    """Meldung des Agenten; ohne vorheriges Auslösen ignoriert."""
    if dev.update_requested_at is None or dev.update_state == "done":
        return
    dev.update_state = msg.state
    dev.update_detail = msg.detail or None
