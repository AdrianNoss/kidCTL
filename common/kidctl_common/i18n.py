"""Internationalisierung: Deutsch (Ausgangssprache im Code) und Englisch (``locale/en.po`` je Bestandteil).

- Sprachregel überall: ``de*`` -> Deutsch, alles andere (auch leer, ``C``, ``fr_FR``) -> Englisch.
- Texte im Code werden mit ``_("…")`` bzw. ``ngettext(…)`` markiert; Platzhalter als ``{name}`` und
  nach der Übersetzung mit ``.format()`` gefüllt. Feste Bezeichnungen in Konstanten mit ``N_`` markieren
  und erst bei der Anzeige übersetzen.
- Die aktuelle Sprache steckt in einer ContextVar (je Anfrage bzw. je Benachrichtigung gesetzt).
- Jeder Bestandteil (Server, Agent, Tray) lädt beim Import seinen eigenen Katalog (``install``).
- Ohne Abhängigkeiten: eigener kleiner .po-Leser statt kompilierter .mo-Dateien.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from datetime import date, datetime
from pathlib import Path

LANGS = ("de", "en")
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("kidctl_lang", default="de")
_catalog: dict[str, str] = {}
_plurals: dict[str, tuple[str, str]] = {}


# ---------- Sprache bestimmen ----------

def normalize(raw: str | None) -> str:
    """``de_DE.UTF-8``/``de-AT``/``de:en`` -> ``de``, alles andere -> ``en``."""
    first = (raw or "").strip().split(":")[0].lower()
    return "de" if first.startswith("de") else "en"


def from_accept_language(header: str | None) -> str:
    """Bevorzugte Browsersprache (höchster q-Wert) nach der Sprachregel."""
    best, best_q = "", -1.0
    for part in (header or "").split(","):
        tag, _sep, params = part.strip().partition(";")
        q = 1.0
        m = re.search(r"q=([0-9.]+)", params)
        if m:
            try:
                q = float(m.group(1))
            except ValueError:
                q = 0.0
        if tag and q > best_q:
            best, best_q = tag, q
    return normalize(best)


def from_environ(env: dict[str, str]) -> str | None:
    """Sprache einer Prozessumgebung (Vorrang wie gettext: LC_ALL, LC_MESSAGES, LANGUAGE, LANG);
    ``None``, wenn nichts gesetzt ist."""
    for key in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        if value := env.get(key, "").strip():
            return normalize(value)
    return None


def parse_locale_conf(text: str) -> dict[str, str]:
    """``/etc/locale.conf`` (KEY=value, Kommentare, Anführungszeichen) -> dict."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            out[key.strip()] = value.strip().strip("'\"")
    return out


def system_language(path: Path | str = "/etc/locale.conf") -> str:
    """Systemsprache (ohne Eintrag: Englisch)."""
    try:
        env = parse_locale_conf(Path(path).read_text())
    except OSError:
        env = {}
    return from_environ(env) or "en"


# ---------- Katalog ----------

def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise ValueError(f"ungültige Zeichenkette: {s!r}")
    return re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s[1:-1])


def parse_po(text: str) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """(Singular: msgid -> msgstr, Plural: msgid -> (Einzahl, Mehrzahl)). Einträge mit ``fuzzy`` und
    leere Übersetzungen entfallen."""
    singular: dict[str, str] = {}
    plural: dict[str, tuple[str, str]] = {}

    def flush(entry: dict) -> None:
        if not entry or entry.get("fuzzy") or not entry.get("msgid"):
            return
        if "msgid_plural" in entry:
            forms = (entry.get("msgstr[0]", ""), entry.get("msgstr[1]", ""))
            if all(forms):
                plural[entry["msgid"]] = forms
        elif entry.get("msgstr"):
            singular[entry["msgid"]] = entry["msgstr"]

    entry: dict = {}
    key = None
    fuzzy = False   # „#, fuzzy“ gilt für den folgenden Eintrag
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            fuzzy = fuzzy or (line.startswith("#,") and "fuzzy" in line)
            continue
        if line.startswith('"'):
            if key is None:
                raise ValueError(f"Fortsetzung ohne Schlüssel: {raw!r}")
            entry[key] += _unquote(line)
            continue
        k, _sep, value = line.partition(" ")
        if k == "msgid":
            flush(entry)
            entry, fuzzy = {"fuzzy": fuzzy}, False
        entry[k] = _unquote(value)
        key = k
    flush(entry)
    return singular, plural


def install(po_file: Path | str) -> None:
    """Englischen Katalog dieses Prozesses laden (fehlende Datei: alles bleibt deutsch)."""
    global _catalog, _plurals
    try:
        _catalog, _plurals = parse_po(Path(po_file).read_text(encoding="utf-8"))
    except OSError:
        _catalog, _plurals = {}, {}


# ---------- Übersetzen ----------

def current() -> str:
    return _lang.get()


@contextlib.contextmanager
def use(lang: str):
    """Sprache für einen Block setzen (z. B. Mail an ein Elternteil, Meldung an ein Kind)."""
    token = _lang.set(lang if lang in LANGS else normalize(lang))
    try:
        yield
    finally:
        _lang.reset(token)


def set_current(lang: str) -> None:
    _lang.set(lang if lang in LANGS else normalize(lang))


def gettext(message: str) -> str:
    if _lang.get() == "de":
        return message
    return _catalog.get(message, message)


def ngettext(singular: str, plural: str, n: int) -> str:
    if _lang.get() == "de":
        return singular if n == 1 else plural
    forms = _plurals.get(singular)
    if forms is None:
        return singular if n == 1 else plural
    return forms[0] if n == 1 else forms[1]


_ = gettext


def N_(message: str) -> str:
    """Nur markieren (Übersetzung später bei der Anzeige)."""
    return message


# ---------- Formate ----------

WEEKDAYS = {"de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
WEEKDAYS_SHORT = {"de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
                  "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
MONTHS = {"de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                 "November", "Dezember"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                 "November", "December"]}


def weekday(d: date, short: bool = True, lang: str | None = None) -> str:
    lang = lang or current()
    return (WEEKDAYS_SHORT if short else WEEKDAYS)[lang][d.weekday()]


def weekday_names(lang: str | None = None, short: bool = False) -> list[str]:
    return (WEEKDAYS_SHORT if short else WEEKDAYS)[lang or current()]


def month_name(month: int, lang: str | None = None) -> str:
    return MONTHS[lang or current()][month - 1]


def fmt_date(d: date, with_weekday: bool = False, year: bool = True, lang: str | None = None) -> str:
    """de: „Di 20.10.2026“ / „20.10.“, en: „Tue 20 Oct 2026“ / „20 Oct“."""
    lang = lang or current()
    if lang == "de":
        text = d.strftime("%d.%m.%Y") if year else d.strftime("%d.%m.")
    else:
        text = f"{d.day} {MONTHS['en'][d.month - 1][:3]}" + (f" {d.year}" if year else "")
    return f"{weekday(d, lang=lang)} {text}" if with_weekday else text


def fmt_datetime(dt: datetime, with_weekday: bool = False, year: bool = True, lang: str | None = None) -> str:
    """de: „20.10.2026 14:03“, en: „20 Oct 2026, 14:03“."""
    lang = lang or current()
    sep = " " if lang == "de" else ", "
    return f"{fmt_date(dt.date(), with_weekday, year, lang)}{sep}{dt:%H:%M}"


def fmt_minutes(minutes: int, lang: str | None = None) -> str:
    """Dauer: de „1:20 h“, en „1 h 20 min“ bzw. „45 min“."""
    lang = lang or current()
    h, m = divmod(max(0, int(minutes)), 60)
    if lang == "de":
        return f"{h}:{m:02d} h"
    if not h:
        return f"{m} min"
    return f"{h} h {m} min" if m else f"{h} h"


def fmt_percent(value: int, signed: bool = False, lang: str | None = None) -> str:
    lang = lang or current()
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value} %" if lang == "de" else f"{sign}{value}%"
