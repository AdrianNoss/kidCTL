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

import errno
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
STEAM_ROOT_OWNER = 0   # /srv/steam gehört root (Tests: eigener User)
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


def _open_dir(name: str, dir_fd: int | None, uid: int | None) -> int:
    """Verzeichnis ohne Symlink-Verfolgung öffnen und (optional) Besitzer prüfen."""
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    if uid is not None and os.fstat(fd).st_uid != uid:
        os.close(fd)
        raise PermissionError(errno.EPERM, "Besitzer passt nicht", name)
    return fd


def move_steam_dir(home: str, uid: int, lib: Path) -> bool:
    """``<home>/.local/share/Steam`` -> ``lib`` per renameat. Jeder Pfadteil wird ohne
    Symlink-Verfolgung geöffnet und muss dem Kind gehören – das Kind kann root so nicht auf fremde
    Verzeichnisse umlenken. ``lib`` muss leer sein (wird ersetzt). False = nichts zu tun."""
    fds = []
    try:
        fds.append(_open_dir(home, None, uid))
        for part in (".local", "share"):
            fds.append(_open_dir(part, fds[-1], uid))
        st = os.stat("Steam", dir_fd=fds[-1], follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
            return False
        fds.append(_open_dir(str(lib.parent), None, STEAM_ROOT_OWNER))
        try:
            os.rmdir(lib.name, dir_fd=fds[-1])
        except FileNotFoundError:
            pass
        try:
            os.rename("Steam", lib.name, src_dir_fd=fds[-2], dst_dir_fd=fds[-1])
        except OSError:
            os.makedirs(lib, mode=0o700, exist_ok=True)   # Ziel wiederherstellen
            os.chown(lib, uid, -1)
            raise
        return True
    finally:
        for fd in fds:
            os.close(fd)


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
        self.steam_pending: set[str] = set()      # Steam-Umzug wartet auf Abmeldung (User)
        self.steam_problems: dict[str, str] = {}  # User -> Problem (für die Integritätsprüfung)
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
        return (any(not self.in_use(h) for h in self.deferred)
                or any(not self.in_use(h) for h in filter(None, map(safe_home, self.steam_pending))))

    # ---------- Steam ----------

    def ensure_steam(self, users: set[str]) -> None:
        """Für alle zugeordneten Kinder (unabhängig vom Programm-Modus): Bibliothek unter STEAM_ROOT,
        ``~/.local/share/Steam`` als Verweis dorthin. Ein bereits vorhandener Steam-Ordner im Home
        wird nach der Abmeldung verschoben – sonst startet Steam bei noexec-Home nicht."""
        self.steam_problems = {u: v for u, v in self.steam_problems.items() if u in users}
        self.steam_pending &= users
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
            r = self._link(pw, home, lib)
            if r.returncode == 3:
                r = self._migrate(user, pw, home, lib)
            if r.returncode == 0:
                self.steam_problems.pop(user, None)
                self.steam_pending.discard(user)
            elif r.returncode != 3:
                log.error("Steam-Link für %s fehlgeschlagen: %s", user, r.stderr.strip())

    def _link(self, pw, home: str, lib: Path) -> subprocess.CompletedProcess:
        """Link als Kind anlegen (nie als root in Kinder-Pfaden schreiben). 3 = anderes Ziel vorhanden."""
        return self.run("setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--init-groups",
                         "--reset-env", "--", "/bin/sh", "-c",
                         'd="$1/.local/share"; l="$d/Steam"; mkdir -p "$d" || exit 2; '
                         'if [ -L "$l" ]; then [ "$(readlink "$l")" = "$2" ] && exit 0; exit 3; fi; '
                         '[ -e "$l" ] && exit 3; ln -s "$2" "$l"', "sh", home, str(lib))

    def _migrate(self, user: str, pw, home: str, lib: Path) -> subprocess.CompletedProcess:
        """Vorhandenen Steam-Ordner aus dem Home nach ``lib`` verschieben (nur abgemeldet, nur per
        Umbenennen auf derselben Partition). Rückgabe wie ``_link``; 3 = nicht (jetzt) möglich."""
        fail = subprocess.CompletedProcess((), 3, "", "")
        if self.in_use(home):
            if user not in self.steam_pending:
                log.warning("Steam-Ordner von %s liegt im Home – wird nach der Abmeldung nach %s verschoben",
                            user, lib)
            self.steam_pending.add(user)
            self.steam_problems[user] = (f"Steam-Ordner liegt im Home – wird nach der Abmeldung nach {lib} "
                                         "verschoben (bis dahin startet Steam bei Programmkontrolle nicht)")
            return fail
        try:
            moved = move_steam_dir(home, pw.pw_uid, lib)
        except OSError as e:
            if e.errno == errno.EXDEV:
                msg = (f"Steam-Ordner liegt im Home, {STEAM_ROOT} auf anderer Partition – bitte von Hand "
                       f"nach {lib} verschieben (siehe Anleitung)")
            elif e.errno == errno.ENOTEMPTY:
                msg = f"Steam-Ordner liegt im Home, {lib} ist aber nicht leer – bitte von Hand zusammenführen"
            else:
                msg = f"Steam-Ordner im Home nicht verschiebbar: {e.strerror}"
            if self.steam_problems.get(user) != msg:
                log.error("%s: %s", user, msg)
            self.steam_problems[user] = msg
            self.steam_pending.discard(user)
            return fail
        if not moved:  # z. B. Verweis auf ein anderes Ziel
            self.steam_problems[user] = (f"{home}/.local/share/Steam ist kein Ordner und zeigt nicht auf {lib} "
                                         "– bitte prüfen")
            return fail
        log.info("Steam-Ordner von %s nach %s verschoben", user, lib)
        os.chmod(lib, 0o700)
        return self._link(pw, home, lib)
