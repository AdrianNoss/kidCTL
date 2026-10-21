"""Installierte Anwendungen aus systemweiten .desktop-Einträgen (für die Auswahl im WebUI).

Bewusst nicht ~/.local/share/applications – die könnte das Kind selbst anlegen.
"""

from __future__ import annotations

import configparser
import logging
import os
import shlex
import shutil
from pathlib import Path

from kidctl_common.protocol import AppEntry

log = logging.getLogger("kidctl.apps")

APP_DIRS = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/flatpak/exports/share/applications",  # nur systemweite Flatpaks
)
SEARCH_PATH = "/usr/local/bin:/usr/bin:/bin"
ETC_SHELLS = "/etc/shells"
# Eigene Einträge (GNOME: „KidCTL“ in der Anwendungsübersicht) – immer erlaubt, keine Eltern-Entscheidung
OWN_KEYS = {"exe:/usr/bin/kidctl-tray"}
# Shells, die Eltern einzeln freigeben können (haben keinen Startmenü-Eintrag)
SELECTABLE_SHELLS = {"sh", "bash", "fish", "zsh", "dash", "ksh", "mksh", "tcsh", "csh", "nu", "elvish", "xonsh"}


def resolve_exec(exec_line: str) -> str | None:
    """Programmpfad (realpath) aus einer Exec-Zeile; ``None``, wenn nicht auflösbar."""
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return None
    tokens = [t for t in tokens if not t.startswith("%")]
    if tokens and os.path.basename(tokens[0]) == "env":  # env VAR=wert [-u X] programm …
        tokens = tokens[1:]
        while tokens and ("=" in tokens[0] or tokens[0].startswith("-")):
            tokens = tokens[1:]
    if not tokens:
        return None
    prog = tokens[0]
    path = prog if os.path.isabs(prog) else shutil.which(prog, path=SEARCH_PATH)
    if not path or not os.path.exists(path):
        return None
    return os.path.realpath(path)


def parse_desktop(path: Path) -> AppEntry | None:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
        e = cp["Desktop Entry"]
    except (configparser.Error, KeyError, UnicodeDecodeError):
        return None
    if e.get("Type", "Application") != "Application":
        return None
    if e.get("NoDisplay", "false").lower() == "true" or e.get("Hidden", "false").lower() == "true":
        return None
    name = e.get("Name[de]") or e.get("Name")
    if not name:
        return None
    if flatpak_id := e.get("X-Flatpak"):
        key = f"flatpak:{flatpak_id}"
    else:
        exe = resolve_exec(e.get("Exec", ""))
        if exe is None:
            return None
        key = f"exe:{exe}"
    categories = [c.strip()[:64] for c in e.get("Categories", "").split(";") if c.strip()][:20]
    return AppEntry(key=key, name=name[:255], icon=e.get("Icon", "")[:255], desktop_id=path.name[:255],
                    categories=categories, name_en=(e.get("Name[en]") or e.get("Name") or "")[:255])


def scan_shells(etc_shells: str = ETC_SHELLS) -> list[AppEntry]:
    """Installierte Shells aus /etc/shells (je Programmdatei einmal, z. B. sh -> bash)."""
    out: dict[str, AppEntry] = {}
    try:
        lines = Path(etc_shells).read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line.startswith("/") or os.path.basename(line) not in SELECTABLE_SHELLS or not os.path.exists(line):
            continue
        real = os.path.realpath(line)
        if os.path.basename(real) not in SELECTABLE_SHELLS:
            continue
        out.setdefault(real, AppEntry(key=f"exe:{real}", name=f"{os.path.basename(real)} (Shell)",
                                      icon="utilities-terminal", categories=["Shell"]))
    return list(out.values())


def scan_apps(dirs=APP_DIRS, etc_shells: str = ETC_SHELLS) -> list[AppEntry]:
    seen: dict[str, AppEntry] = {}
    for d in dirs:
        base = Path(d)
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.desktop")):
            app = parse_desktop(f)
            if app and app.key not in seen and app.key not in OWN_KEYS:
                seen[app.key] = app
    for sh in scan_shells(etc_shells):
        seen.setdefault(sh.key, sh)
    return sorted(seen.values(), key=lambda a: a.name.lower())
