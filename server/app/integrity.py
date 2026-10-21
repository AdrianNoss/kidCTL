"""Integritätsmeldungen der Agenten speichern und bei neuen Fehlern die Eltern alarmieren."""

from __future__ import annotations

import json
import logging

from kidctl_common import protocol as p

from . import push
from .i18n import N_, _
from .models import Device, utcnow

log = logging.getLogger("kidctl.integrity")


def apply_report(dev: Device, msg: p.IntegrityReport) -> list[p.IntegrityCheck]:
    """Speichert den Zustand; gibt die *neu* fehlschlagenden Prüfungen zurück (kein Commit)."""
    before = {c["id"] for c in dev.integrity_failed}
    dev.integrity_json = json.dumps([c.model_dump() for c in msg.checks], ensure_ascii=False)
    dev.integrity_at = utcnow()
    new = [c for c in msg.checks if not c.ok and c.id not in before]
    for c in new:
        log.warning("Integritätsfehler auf Gerät %s (%s): %s – %s", dev.id, dev.name, c.label, c.detail)
    return new


def alarm(dev: Device, new: list[p.IntegrityCheck]) -> None:
    if not new:
        return
    checks = [c.model_dump() for c in new]
    push.notify_parents(lambda: _("Integritätsfehler auf „{name}“").format(name=dev.name),
                        lambda: ", ".join(label_for(c) for c in checks)[:250],
                        url=f"/devices/{dev.id}", tag=f"integrity-{dev.id}")


_LIBRARIES = {"steam": N_("Steam-Bibliothek"), "heroic": N_("Heroic-Spieleordner")}


def label_for(check: dict) -> str:
    """Bezeichnung einer Prüfung in der aktuellen Sprache. Der Agent liefert sie deutsch; für andere
    Sprachen wird sie aus der Kennung gebildet (unbekannte Kennung: Text des Agenten)."""
    from .i18n import current
    label = str(check.get("label", ""))
    if current() == "de":
        return label
    cid = str(check.get("id", ""))
    kind, _sep, arg = cid.partition(":")
    fixed = {"pam": _("Login-Sperre (PAM)"), "nsswitch": _("Namensauflösung über DNS-Filter (nsswitch)"),
             "browser": _("Browser-Policies gegen DNS-over-HTTPS"),
             "noexec": _("noexec für /tmp, /var/tmp, /dev/shm")}
    if cid in fixed:
        return fixed[cid]
    if kind == "home" and arg:
        return _("noexec-Home von {user}").format(user=arg)
    if kind == "noexec" and arg:
        return _("noexec-Spieleordner {path}").format(path=arg)
    if kind == "desktop" and arg:
        return _("Desktop-Paket kidctl-{desktop}").format(desktop=arg)
    if kind == "gdm-autologin" and arg:
        return _("GDM-Autologin für {user}").format(user=arg)
    if kind == "tray" and arg:
        return _("Tray-Symbol von {user}").format(user=arg)
    if kind == "dns" and arg:
        return _("Website-Filter Kind {id}").format(id=arg)
    if kind in _LIBRARIES and arg:
        return _("{library} von {user}").format(library=_(_LIBRARIES[kind]), user=arg)
    return label
