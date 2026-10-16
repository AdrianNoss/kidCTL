"""noexec für Kinder-Homes und Steam-Bibliothek außerhalb des Homes.

- Home jedes zugeordneten Kindes wird per Bind-Mount auf sich selbst mit ``noexec,nosuid,nodev``
  eingehängt. Über ``systemd-mount`` (transiente Unit in PID 1): Der Agent läuft in einem eigenen
  Mount-Namespace (systemd-Härtung) – eigene mount()-Aufrufe wären systemweit nicht sichtbar.
  Transiente Units überleben keinen Neustart; der Agent legt sie beim Start vor den Logins neu an.
- Steam: Bibliothek unter ``/srv/steam/<user>`` (ausführbar, gehört dem Kind);
  ``~/.local/share/Steam`` ist ein Symlink dorthin. Den Link legt der Agent mit den Rechten des
  Kindes an – root schreibt/mountet nie in Pfade, die das Kind kontrolliert (Symlink-Tricks).
"""

from __future__ import annotations

import logging
import os
import pwd
import stat
import subprocess
from pathlib import Path

log = logging.getLogger("kidctl.mounts")

DESCRIPTION = "kidctl: noexec-Home"
HOME_PARENT = "/home"
STEAM_ROOT = Path(os.environ.get("KIDCTL_STEAM_ROOT", "/srv/steam"))
STEAM_INSTALLED = Path("/usr/lib/steam")
MOUNTINFO = "/proc/self/mountinfo"


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin", "LC_ALL": "C"})


def _unescape(path: str) -> str:
    return path.encode().decode("unicode_escape") if "\\" in path else path


def home_in_use(home: str) -> bool:
    """Ist der Besitzer des Homes angemeldet? (Laufzeitverzeichnis existiert, solange eine Sitzung
    oder der user@-Manager läuft.) Ein Home darf dann nicht ausgehängt werden – das reißt die laufende
    Plasma-Sitzung mit (schwarzer Bildschirm)."""
    try:
        uid = os.stat(home).st_uid
    except OSError:
        return False
    return os.path.exists(f"/run/user/{uid}")


def noexec_mounts() -> set[str]:
    """Mountpunkte mit noexec (aus /proc/self/mountinfo)."""
    out = set()
    try:
        lines = Path(MOUNTINFO).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        f = line.split()
        if len(f) > 5 and "noexec" in f[5].split(","):
            out.add(_unescape(f[4]))
    return out


def safe_home(user: str) -> str | None:
    """Home nur, wenn es genau /home/<name> ist, kein Symlink, ein Verzeichnis des Users."""
    try:
        pw = pwd.getpwnam(user)
        st = os.lstat(pw.pw_dir)
    except (KeyError, OSError):
        return None
    home = os.path.normpath(pw.pw_dir)
    if os.path.dirname(home) != HOME_PARENT or not stat.S_ISDIR(st.st_mode) or st.st_uid != pw.pw_uid:
        return None
    return home


class KidMounts:
    def __init__(self, run=_run, mounts=noexec_mounts, in_use=home_in_use) -> None:
        self.run = run
        self.mounts = mounts
        self.in_use = in_use
        self.deferred: set[str] = set()  # Aushängen aufgeschoben, bis der Besitzer abgemeldet ist
        self._warned: set[str] = set()

    def unit_for(self, path: str) -> str:
        r = self.run("systemd-escape", "--path", "--suffix=mount", path)
        return r.stdout.strip()

    def ours(self) -> set[str]:
        """Von kidctl angelegte, aktive noexec-Mounts unter /home."""
        out = set()
        try:
            names = os.listdir(HOME_PARENT)
        except OSError:
            return out
        for name in names:
            path = os.path.join(HOME_PARENT, name)
            r = self.run("systemctl", "show", self.unit_for(path), "-p", "Description", "-p", "ActiveState",
                         "--value")
            desc, _, state = r.stdout.partition("\n")
            if desc.strip() == DESCRIPTION and state.strip() == "active":
                out.add(path)
        return out

    def sync(self, users: set[str]) -> None:
        """Soll-Zustand: genau die Homes von ``users`` sind noexec (blockierend – im Thread aufrufen)."""
        wanted = {}
        for user in users:
            home = safe_home(user)
            if home is None:
                if user not in self._warned:
                    log.warning("Home von %s nicht unter %s/<name> oder unsicher – kein noexec", user, HOME_PARENT)
                    self._warned.add(user)
                continue
            wanted[home] = user
        active = self.mounts()
        for home, user in wanted.items():
            if home in active:
                continue
            r = self.run("systemd-mount", "--options=bind,noexec,nosuid,nodev", f"--description={DESCRIPTION}",
                         home, home)
            if r.returncode == 0:
                log.info("Home von %s noexec eingehängt", user)
            else:
                log.error("noexec für %s fehlgeschlagen: %s", home, r.stderr.strip())
        self.deferred -= wanted.keys()
        for home in self.ours() - wanted.keys():
            if self.in_use(home):
                if home not in self.deferred:
                    log.info("noexec für %s wird nach der Abmeldung aufgehoben (Sitzung läuft)", home)
                    self.deferred.add(home)
                continue
            self.deferred.discard(home)
            r = self.run("systemd-umount", home)
            log.info("noexec für %s aufgehoben%s", home, "" if r.returncode == 0 else f": {r.stderr.strip()}")

    def releasable(self) -> bool:
        """Ist ein aufgeschobenes Home inzwischen frei (Besitzer abgemeldet)?"""
        return any(not self.in_use(h) for h in self.deferred)

    # ---------- Steam ----------

    def ensure_steam(self, users: set[str]) -> None:
        if not STEAM_INSTALLED.exists():
            return
        STEAM_ROOT.mkdir(mode=0o755, exist_ok=True)
        for user in users:
            home = safe_home(user)
            if home is None:
                continue
            pw = pwd.getpwnam(user)
            lib = STEAM_ROOT / user
            if not lib.exists():
                lib.mkdir(mode=0o700)
            st = os.lstat(lib)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                log.error("%s ist kein Verzeichnis – Steam-Bibliothek übersprungen", lib)
                continue
            if st.st_uid != pw.pw_uid:
                os.chown(lib, pw.pw_uid, pw.pw_gid)
            # Link als Kind anlegen (nie als root in Kinder-Pfaden schreiben)
            r = self.run("setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--init-groups",
                         "--reset-env", "--", "/bin/sh", "-c",
                         'd="$1/.local/share"; l="$d/Steam"; mkdir -p "$d" || exit 2; '
                         'if [ -L "$l" ]; then [ "$(readlink "$l")" = "$2" ] && exit 0; exit 3; fi; '
                         '[ -e "$l" ] && exit 3; ln -s "$2" "$l"', "sh", home, str(lib))
            if r.returncode == 3 and user not in self._warned:
                self._warned.add(user)
                log.warning("%s/.local/share/Steam existiert bereits und zeigt nicht auf %s – Steam "
                            "startet dort nicht (Home ist noexec). Inhalt nach %s verschieben.", home, lib, lib)
            elif r.returncode not in (0, 3):
                log.error("Steam-Link für %s fehlgeschlagen: %s", user, r.stderr.strip())
