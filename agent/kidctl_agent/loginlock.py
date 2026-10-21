"""Sperrliste für die PAM-Login-Sperre.

PAM (``pam_listfile`` in /etc/pam.d/system-login) lehnt Anmeldungen der hier gelisteten User ab
und zeigt vorher ``locked-message`` (``pam_echo``). Fehlt die Datei, ist die Anmeldung erlaubt
(``onerr=succeed``) – ein ausgefallener Agent sperrt niemanden aus.
"""

from __future__ import annotations

import grp
import logging
import os
import pwd
import re
import tempfile
from pathlib import Path

from kidctl_common import i18n
from kidctl_common.i18n import N_
from kidctl_common.protocol import USERNAME_PATTERN

from .lang import system_language

log = logging.getLogger("kidctl.loginlock")

RUN_DIR = Path(os.environ.get("KIDCTL_RUN_DIR", "/run/kidctl"))
LOCKED_FILE = "locked-users"
MESSAGE_FILE = "locked-message"
MESSAGE = N_("Bildschirmzeit für heute aufgebraucht, außerhalb der erlaubten Zeit oder von den Eltern "
             "gesperrt. Bitte bei den Eltern melden.")   # Sprache: /etc/locale.conf (vor der Anmeldung)

ADMIN_GROUPS = ("wheel", "sudo", "admin")


def is_admin(username: str) -> bool:
    """root und Mitglieder von wheel/sudo/admin werden nie eingeschränkt."""
    try:
        pw = pwd.getpwnam(username)
    except KeyError:
        return False
    if pw.pw_uid == 0:
        return True
    for name in ADMIN_GROUPS:
        try:
            g = grp.getgrnam(name)
        except KeyError:
            continue
        if username in g.gr_mem or pw.pw_gid == g.gr_gid:
            return True
    return False


def _write(name: str, content: str) -> None:
    RUN_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=RUN_DIR, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o644)  # pam_listfile verlangt: nicht world-writable
            f.write(content)
        os.replace(tmp, RUN_DIR / name)
    except BaseException:
        os.unlink(tmp)
        raise


class LoginLock:
    def __init__(self) -> None:
        self._current: list[str] | None = None

    def update(self, locked: set[str]) -> None:
        # Doppelte Absicherung: nur gültige Namen (eine Zeile je User) und nie Admins
        users = sorted(u for u in locked if re.fullmatch(USERNAME_PATTERN, u) and not is_admin(u))
        if users == self._current:
            return
        if self._current is None:
            with i18n.use(system_language()):
                _write(MESSAGE_FILE, i18n.gettext(MESSAGE) + "\n")
        _write(LOCKED_FILE, "".join(f"{u}\n" for u in users))
        if users != (self._current or []):
            log.info("Login gesperrt für: %s", ", ".join(users) or "niemanden")
        self._current = users
