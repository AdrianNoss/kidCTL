"""Desktop-Verknüpfungen für erlaubte Programme vertrauenswürdig machen.

KDE startet ``.desktop``-Dateien außerhalb der Anwendungsordner nur, wenn sie ausführbar sind oder
root gehören (``KDesktopFile::isAuthorizedDesktopFile``). Bei Programmkontrolle ist das Home
``noexec`` – damit gilt keine Verknüpfung mehr als ausführbar (Qt prüft per ``access(X_OK)``), auch
nicht die von Steam oder Heroic angelegten Spiel-Verknüpfungen.

Der Agent übernimmt deshalb Verknüpfungen auf dem Desktop des Kindes, deren Programm das Kind starten
darf, für root (``0644``): KDE vertraut ihnen, das Kind kann sie löschen, aber nicht mehr ändern.
Die eigentliche Durchsetzung bleibt beim Agenten (jeder Programmstart wird geprüft).

Sicherheit: Jede Datei wird ohne Symlink-Verfolgung geöffnet und nur per ``fchown`` auf dem offenen
Deskriptor geändert; nur reguläre Dateien des Kindes ohne weitere harte Links.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import stat
from collections.abc import Callable

from .apps import resolve_exec
from .programs import SHELLS, SYSTEM_SCRIPT_DIRS

log = logging.getLogger("kidctl.desktop")

MAX_SIZE = 64 * 1024
USER_DIRS = ".config/user-dirs.dirs"

# ("flatpak", App-ID), ("snap", Snap-Name) oder ("exe", Programmpfad)
Target = tuple[str, str]


def entry_target(text: str) -> Target | None:
    """Was eine Verknüpfung startet; ``None`` bei Nicht-Anwendungen oder unauflösbarem Exec."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read_string(text)
        e = cp["Desktop Entry"]
    except (configparser.Error, KeyError):
        return None
    if e.get("Type", "Application") != "Application":
        return None
    if flatpak_id := e.get("X-Flatpak"):
        return ("flatpak", flatpak_id)
    if snap := e.get("X-SnapInstanceName"):
        return ("snap", snap)
    exe = resolve_exec(e.get("Exec", ""))
    return ("exe", exe) if exe else None


def _system_shell_script(path: str) -> bool:
    """Shell-Skript aus einem Systemverzeichnis (z. B. xdg-open) – erlaubt der Agent immer."""
    if not path.startswith(SYSTEM_SCRIPT_DIRS):
        return False
    try:
        with open(path, "rb") as f:
            head = f.readline(256)
    except OSError:
        return False
    m = re.match(rb"#!\s*(\S+)(?:\s+(\S+))?", head)
    if not m:
        return False
    interp = os.path.basename(m.group(1).decode(errors="replace"))
    if interp == "env" and m.group(2):
        interp = os.path.basename(m.group(2).decode(errors="replace"))
    return interp in SHELLS


def target_allowed(target: Target, allowed) -> bool:
    kind, value = target
    if kind == "flatpak":
        return value in allowed.flatpaks
    if kind == "snap":
        return value in allowed.snaps
    return allowed.path_ok(value) or _system_shell_script(value)


def desktop_dirs(home: str) -> list[str]:
    """Desktop-Ordner des Kindes: ``~/Desktop`` und ``XDG_DESKTOP_DIR`` (z. B. ``~/Schreibtisch``),
    nur direkte Unterordner des Homes."""
    names = ["Desktop"]
    try:
        with open(os.path.join(home, USER_DIRS), encoding="utf-8", errors="replace") as f:
            text = f.read(16 * 1024)
        m = re.search(r'^XDG_DESKTOP_DIR="\$HOME/([^"/]+)"\s*$', text, re.M)
        if m and m.group(1) not in names and m.group(1) not in (".", ".."):
            names.append(m.group(1))
    except OSError:
        pass
    return [os.path.join(home, n) for n in names]


def _open_dir(path: str, uid: int) -> int | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    if os.fstat(fd).st_uid != uid:
        os.close(fd)
        return None
    return fd


def trust_desktop(home: str, uid: int, is_allowed: Callable[[Target], bool]) -> list[str]:
    """Verknüpfungen erlaubter Programme root übergeben. Gibt die neu vertrauten Dateinamen zurück."""
    trusted = []
    home_fd = _open_dir(home, uid)
    if home_fd is None:
        return trusted
    try:
        for path in desktop_dirs(home):
            dfd = None
            try:
                dfd = os.open(os.path.basename(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
                if os.fstat(dfd).st_uid != uid:
                    continue
                for name in os.listdir(dfd):
                    if name.endswith(".desktop") and _trust_one(dfd, name, uid, is_allowed):
                        trusted.append(os.path.join(path, name))
            except OSError:
                continue
            finally:
                if dfd is not None:
                    os.close(dfd)
    finally:
        os.close(home_fd)
    return trusted


def _trust_one(dfd: int, name: str, uid: int, is_allowed: Callable[[Target], bool]) -> bool:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_nlink != 1
                or st.st_size > MAX_SIZE):
            return False  # schon vertraut (root), fremd, harter Link oder zu groß
        text = os.read(fd, MAX_SIZE + 1).decode("utf-8", errors="replace")
        target = entry_target(text)
        if target is None or not is_allowed(target):
            return False
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o644)
        log.info("Desktop-Verknüpfung %s vertraut (%s %s)", name, *target)
        return True
    except OSError as e:
        log.warning("Desktop-Verknüpfung %s nicht übernommen: %s", name, e)
        return False
    finally:
        os.close(fd)
