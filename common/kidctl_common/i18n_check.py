"""Prüfhilfe für Tests: markierte Texte einsammeln und gegen einen Katalog prüfen.

Erfasst ``_("…")``, ``gettext("…")``, ``N_("…")`` und ``ngettext("…", "…", n)`` mit festen Zeichenketten in
Python-Dateien (ast) und Jinja-Templates (Jinja-i18n-Extraktion). Variable Argumente (``_(label)``)
werden übersprungen – deren Texte müssen an der Quelle mit ``N_`` markiert sein.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .i18n import parse_po

FUNCS = {"_", "gettext", "N_", "ngettext"}
_PLACEHOLDER = re.compile(r"\{(\w*)\}")


def _const(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def extract_python(files) -> dict[str, str | None]:
    """msgid -> Plural-msgid (oder None)."""
    out: dict[str, str | None] = {}
    for f in files:
        tree = ast.parse(Path(f).read_text(encoding="utf-8"), str(f))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS):
                continue
            if not node.args or (msg := _const(node.args[0])) is None:
                continue
            plural = _const(node.args[1]) if node.func.id == "ngettext" and len(node.args) > 1 else None
            out[msg] = plural
    return out


def extract_jinja(template_dir) -> dict[str, str | None]:
    from jinja2 import Environment
    env = Environment(extensions=["jinja2.ext.i18n"])
    out: dict[str, str | None] = {}
    for f in sorted(Path(template_dir).rglob("*")):
        if f.suffix not in (".html", ".txt") or not f.is_file():
            continue
        for _line, func, message in env.extract_translations(f.read_text(encoding="utf-8"),
                                                              gettext_functions=tuple(FUNCS)):
            if isinstance(message, str):
                out[message] = None
            elif isinstance(message, tuple) and message and isinstance(message[0], str):
                out[message[0]] = message[1] if len(message) > 1 and isinstance(message[1], str) else None
    return out


def problems(po_file, msgids: dict[str, str | None]) -> list[str]:
    """Fehlende Übersetzungen und abweichende Platzhalter (leer = alles in Ordnung)."""
    singular, plural = parse_po(Path(po_file).read_text(encoding="utf-8"))
    out = []
    for msg, plural_msg in sorted(msgids.items()):
        if plural_msg is not None:
            forms = plural.get(msg)
            if forms is None:
                out.append(f"fehlt (Plural): {msg!r}")
                continue
            targets = forms
        else:
            target = singular.get(msg)
            if target is None:
                out.append(f"fehlt: {msg!r}")
                continue
            targets = (target,)
        want = set(_PLACEHOLDER.findall(msg)) | set(_PLACEHOLDER.findall(plural_msg or ""))
        for t in targets:
            if set(_PLACEHOLDER.findall(t)) != want:
                out.append(f"Platzhalter weichen ab: {msg!r} -> {t!r}")
    return out


def unused(po_file, msgids: dict[str, str | None]) -> list[str]:
    singular, plural = parse_po(Path(po_file).read_text(encoding="utf-8"))
    return sorted((set(singular) | set(plural)) - set(msgids))
