"""Anzeigetexte und Formatierung (ohne Qt, testbar). Sprache: Sitzung des Kindes (Umgebung der Tray-App),
sonst ``/etc/locale.conf``; ``de*`` -> Deutsch, sonst Englisch."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from kidctl_common import i18n
from kidctl_common import tray as t
from kidctl_common.i18n import N_, _, ngettext
from kidctl_common.protocol import ChatMessage

i18n.install(Path(__file__).parent / "locale" / "en.po")

REASON = {
    "quota": N_("Bildschirmzeit für heute aufgebraucht"),
    "window_not_open": N_("Die erlaubte Zeit hat noch nicht begonnen"),
    "window_closed": N_("Die erlaubte Zeit ist für heute vorbei"),
    "locked": N_("Von deinen Eltern gesperrt"),
}


def setup_language(environ=os.environ) -> str:
    """Sprache der Tray-App festlegen (einmal beim Start)."""
    if sys.platform == "win32":
        from .winplatform import user_language
        lang = user_language()
    else:
        lang = i18n.from_environ(dict(environ)) or i18n.system_language()
    i18n.set_current(lang)
    return lang


def fmt_duration(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}:{minutes % 60:02d} h" if i18n.current() == "de" else i18n.fmt_minutes(minutes)


def level(state: t.TrayState) -> str:
    if not state.allowed or state.remaining_seconds <= 5 * 60:
        return "critical"
    if state.remaining_seconds <= 15 * 60:
        return "warning"
    return "ok"


def status_line(state: t.TrayState) -> str:
    if not state.allowed:
        reason = REASON.get(state.reason or "")
        return _(reason) if reason else _("Keine Bildschirmzeit")
    if state.window_end:
        return _("Noch {time} (heute bis {end} Uhr)").format(time=fmt_duration(state.remaining_seconds),
                                                            end=f"{state.window_end:%H:%M}")
    return _("Noch {time}").format(time=fmt_duration(state.remaining_seconds))


def detail_lines(state: t.TrayState) -> list[str]:
    lines = [status_line(state),
             _("Heute genutzt: {used} von {allowed}").format(used=fmt_duration(state.used_seconds),
                                                             allowed=fmt_duration(state.allowed_minutes * 60))]
    if state.window_start and state.window_end:
        lines.append(_("Erlaubt: {start}–{end} Uhr").format(start=f"{state.window_start:%H:%M}",
                                                            end=f"{state.window_end:%H:%M}"))
    if state.game_limit_minutes is not None:
        left = max(0, state.game_limit_minutes * 60 - state.game_used_seconds)
        lines.append(_("Spielzeit: noch {left} von {total}").format(
            left=fmt_duration(left), total=fmt_duration(state.game_limit_minutes * 60))
            if left else _("Spielzeit für heute aufgebraucht"))
    if not state.online:
        lines.append(_("Offline – Nachrichten werden gesendet, sobald Verbindung besteht"))
    elif state.pending_outbox:
        lines.append(ngettext("{n} Nachricht wird gesendet …", "{n} Nachrichten werden gesendet …",
                                   state.pending_outbox).format(n=state.pending_outbox))
    return lines


def local_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return i18n.fmt_datetime(dt.astimezone(), year=False)


def _until(t_) -> str:
    return _("bis {time} Uhr").format(time=f"{t_:%H:%M}")


def request_summary(m: ChatMessage) -> str:
    if m.request_target == "games":
        return _("+{n} min Spielzeit").format(n=m.requested_minutes or 0)
    parts = []
    if m.requested_minutes:
        parts.append(f"+{m.requested_minutes} min")
    if m.requested_window_end:
        parts.append(_until(m.requested_window_end))
    return _(" und ").join(parts)


def request_status(m: ChatMessage) -> str:
    if m.status == "approved":
        parts = []
        if m.approved_minutes:
            parts.append(f"+{m.approved_minutes} min")
        if m.approved_window_end:
            parts.append(_until(m.approved_window_end))
        return "✔ " + _("Genehmigt") + (f": {_(' und ').join(parts)}" if parts else "")
    return {"pending": "⏳ " + _("Wartet auf Antwort"), "rejected": "✘ " + _("Abgelehnt"),
            "expired": _("Verfallen (Tag vorbei)")}.get(m.status or "", "")


def kind_label(kind: str | None) -> str:
    return _("Website") if kind == "domain" else _("Programm")


def access_status(m: ChatMessage) -> str:
    if m.status == "approved":
        return "✔ " + (_("Heute erlaubt") if m.access_scope == "today" else _("Dauerhaft erlaubt"))
    return {"pending": "⏳ " + _("Wartet auf Antwort"), "rejected": "✘ " + _("Abgelehnt")}.get(m.status or "", "")


def blocked_line(item: t.BlockedItem) -> str:
    """„Website · zuletzt 14:03 · 3×“"""
    parts = [kind_label(item.access_kind), _("zuletzt {time}").format(time=local_time(item.last_at))]
    if item.count > 1:
        parts.append(f"{item.count}×")
    return " · ".join(parts)


def paused_line() -> str:
    """Ruhemodus: Abo der Familie abgelaufen – nichts wird eingeschränkt."""
    return _("KidCTL ist pausiert – keine Einschränkungen")


def first_notice() -> str:
    """Einmaliger Hinweis beim ersten Anmelden nach der Zuordnung (offener Betrieb, kein verdeckter Modus)."""
    return "\n\n".join([
        _("Dieser Account wird von KidCTL verwaltet."),
        _("Deine Eltern legen fest, wann und wie lange du den Rechner nutzen kannst und welche Programme und Websites "
          "erlaubt sind."),
        _("Erfasst werden: Bildschirmzeit, genutzte Programme und Spiele mit ihrer Dauer, gesperrte Programme und "
          "Websites sowie die Nachrichten im Chat mit deinen Eltern."),
        _("Nicht erfasst werden: Inhalte von Websites, welche erlaubten Websites du besuchst, deine Eingaben, "
          "Bildschirminhalte und Dateien."),
        _("Das KidCTL-Symbol in der Taskleiste zeigt dir jederzeit deine Restzeit. Dort kannst du deinen Eltern auch "
          "schreiben und mehr Zeit anfragen."),
    ])


HOMEPAGE = "https://kidctl.de/"
SOURCE_URL = "https://github.com/AdrianNoss/kidCTL"
LICENSE_URL = "https://www.gnu.org/licenses/agpl-3.0.html"


def version() -> str:
    """Installierte KidCTL-Version: Agent und Tray kommen aus demselben Paket, maßgeblich ist der Agent."""
    try:
        from kidctl_agent import __version__
    except ImportError:   # nur Tray installiert (Entwicklung)
        from . import __version__
    return __version__


def about_html() -> str:
    """Inhalt des Info-Fensters (Linux und Windows gleich): Version und Links, kein Sicherheitskontakt für Kinder."""
    links = " · ".join(f'<a href="{url}">{label}</a>' for url, label in (
        (HOMEPAGE, _("Projektseite")), (SOURCE_URL, _("Quellcode")), (LICENSE_URL, _("Lizenz: AGPL-3.0"))))
    return (f"<p>{_('Version {version}').format(version=version())}</p>"
            f"<p>{_('KidCTL ist freie Software zur Begleitung der Bildschirmzeit.')}</p><p>{links}</p>")
