"""noexec für Kinder-Homes und Spiele-Bibliotheken außerhalb des Homes.

- Home jedes zugeordneten Kindes wird per Bind-Mount auf sich selbst mit ``noexec,nosuid,nodev``
  eingehängt. Über ``systemd-mount`` (transiente Unit in PID 1): Der Agent läuft in einem eigenen
  Mount-Namespace (systemd-Härtung) – eigene mount()-Aufrufe wären systemweit nicht sichtbar.
  Transiente Units überleben keinen Neustart; der Agent legt sie beim Start vor den Logins neu an.
- Steam: Bibliothek unter ``/srv/steam/<user>`` (ausführbar, gehört dem Kind);
  ``~/.local/share/Steam`` ist ein Symlink dorthin. Den Link legt der Agent mit den Rechten des
  Kindes an – root schreibt/mountet nie in Pfade, die das Kind kontrolliert (Symlink-Tricks).
- Spieleordner eines Kindes, dessen Launcher (Steam/Heroic) nicht freigegeben ist, werden ebenfalls
  ``noexec`` eingehängt: Sonst könnte das Kind dort eigene Bibliotheken ablegen und in erlaubte Apps
  laden (``LD_PRELOAD``, Vulkan-Layer in ``~/.local/share/vulkan``) – die Programmprüfung sieht das nicht.
- Heroic genauso: ``/srv/heroic/<user>`` mit ``~/Games/Heroic`` (Spiele, Wine-Prefixe) -> ``Games``
  ``~/.config/heroic/tools`` (Wine, Proton) -> ``tools`` und ``~/.local/share/umu`` (Laufzeit, mit
  der Heroic Proton startet) -> ``umu``, ``~/.cache/heroic_gogdl`` und ``~/.cache/legendary`` (native
  Module von gogdl/legendary) -> ``cache/…``.
"""

from __future__ import annotations

import errno
import logging
import os
import pwd
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kidctl.mounts")

DESCRIPTION = "kidctl: noexec-Home"
LIB_DESCRIPTION = "kidctl: noexec-Spieleordner"
HOME_PARENT = "/home"
STEAM_ROOT = Path(os.environ.get("KIDCTL_STEAM_ROOT", "/srv/steam"))
STEAM_INSTALLED = Path("/usr/lib/steam")
STEAM_ROOT_OWNER = 0   # /srv/steam, /srv/heroic gehören root (Tests: eigener User)
HEROIC_ROOT = Path(os.environ.get("KIDCTL_HEROIC_ROOT", "/srv/heroic"))
HEROIC_INSTALLED = Path("/usr/bin/heroic")
MOUNTINFO = "/proc/self/mountinfo"


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C"})


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


@dataclass(frozen=True)
class Library:
    """Spiele-Bibliothek unter ``root/<user>``; ``links``: (Pfad im Home, Unterordner der Bibliothek,
    leer = Bibliothek selbst). ``caches``: Pfade im Home, die statt Umzug gelöscht werden (werden neu
    erzeugt)."""
    key: str
    label: str
    exe: str        # Launcher (Freigabe-Schlüssel exe:…)
    installed: Path
    root: Path
    links: tuple[tuple[str, str], ...]
    caches: frozenset[str] = frozenset()


def libraries() -> list[Library]:
    return [Library("steam", "Steam", "/usr/bin/steam", STEAM_INSTALLED, STEAM_ROOT, ((".local/share/Steam", ""),)),
            Library("heroic", "Heroic", "/usr/bin/heroic", HEROIC_INSTALLED, HEROIC_ROOT,
                    (("Games/Heroic", "Games"), (".config/heroic/tools", "tools"),
                     (".local/share/umu", "umu"),
                     # gogdl/legendary entpacken native Module (.so) dorthin und laden sie
                     (".cache/heroic_gogdl", "cache/heroic_gogdl"), (".cache/legendary", "cache/legendary")),
                    caches=frozenset({".cache/heroic_gogdl", ".cache/legendary"}))]


def move_dir(home: str, uid: int, rel: str, lib: Path, sub: str) -> bool:
    """``<home>/<rel>`` -> ``lib`` bzw. ``lib/<sub>`` per renameat. Jeder Pfadteil wird ohne
    Symlink-Verfolgung geöffnet und muss dem Kind gehören (Ziel-Elternordner: root bzw. Kind) – das
    Kind kann root so nicht auf fremde Verzeichnisse umlenken. Das Ziel muss leer sein (wird
    ersetzt). False = nichts zu tun."""
    *parts, name = rel.split("/")
    fds = []
    try:
        fds.append(_open_dir(home, None, uid))
        for part in parts:
            fds.append(_open_dir(part, fds[-1], uid))
        st = os.stat(name, dir_fd=fds[-1], follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
            return False
        src = fds[-1]
        if sub:  # Zwischenordner (z. B. cache/) ebenfalls ohne Symlink-Verfolgung, Besitzer Kind
            *sub_parts, dst_name = sub.split("/")
            fds.append(_open_dir(str(lib), None, uid))
            for part in sub_parts:
                fds.append(_open_dir(part, fds[-1], uid))
        else:
            fds.append(_open_dir(str(lib.parent), None, STEAM_ROOT_OWNER))
            dst_name = lib.name
        try:
            os.rmdir(dst_name, dir_fd=fds[-1])
        except FileNotFoundError:
            pass
        try:
            os.rename(name, dst_name, src_dir_fd=src, dst_dir_fd=fds[-1])
        except OSError:
            os.mkdir(dst_name, mode=0o700, dir_fd=fds[-1])   # Ziel wiederherstellen
            os.chown(dst_name, uid, -1, dir_fd=fds[-1], follow_symlinks=False)
            raise
        if not sub:  # Bibliothek selbst (Elternordner gehört root): nur das Kind darf hinein
            os.chmod(lib, 0o700)
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
        self.lib_pending: set[tuple[str, str]] = set()       # Umzug wartet auf Abmeldung (User, Pfad)
        self.lib_problems: dict[tuple[str, str], str] = {}  # (Bibliothek, User) -> Problem (Integrität)
        self._warned: set[str] = set()

    def unit_for(self, path: str) -> str:
        r = self.run("systemd-escape", "--path", "--suffix=mount", path)
        return r.stdout.strip()

    def ours(self, parents=None, description: str = DESCRIPTION) -> set[str]:
        """Von kidctl angelegte, aktive noexec-Mounts (Standard: Homes unter /home)."""
        out = set()
        for parent in parents if parents is not None else [HOME_PARENT]:
            try:
                names = os.listdir(parent)
            except OSError:
                continue
            for name in names:
                path = os.path.join(parent, name)
                r = self.run("systemctl", "show", self.unit_for(path), "-p", "Description", "-p", "ActiveState",
                             "--value")
                desc, _, state = r.stdout.partition("\n")
                if desc.strip() == description and state.strip() == "active":
                    out.add(path)
        return out

    def sync_libraries(self, wanted: set[str]) -> None:
        """Soll-Zustand: genau die Spieleordner ``wanted`` (``<root>/<user>``) sind noexec. Aushängen
        sofort – dort läuft nichts (war noexec), keine Sitzung hängt daran; ist der Ordner belegt,
        scheitert systemd-umount und der nächste Abgleich versucht es erneut."""
        active = self.mounts()
        for path in sorted(wanted - active):
            try:
                st = os.lstat(path)
                parent = os.lstat(os.path.dirname(path))
            except OSError:
                continue
            if not stat.S_ISDIR(st.st_mode) or parent.st_uid != STEAM_ROOT_OWNER:
                log.error("%s ist kein Verzeichnis unter einem root-Ordner – kein noexec", path)
                continue
            r = self.run("systemd-mount", "--options=bind,noexec,nosuid,nodev", f"--description={LIB_DESCRIPTION}",
                         path, path)
            log.log(logging.INFO if r.returncode == 0 else logging.ERROR, "Spieleordner %s noexec%s", path,
                    "" if r.returncode == 0 else f" fehlgeschlagen: {r.stderr.strip()}")
        for path in self.ours([str(lib.root) for lib in libraries()], LIB_DESCRIPTION) - wanted:
            r = self.run("systemd-umount", path)
            log.info("noexec für Spieleordner %s aufgehoben%s", path, "" if r.returncode == 0 else f": {r.stderr.strip()}")

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
                or any(not self.in_use(h) for h in filter(None, (safe_home(u) for u, _ in self.lib_pending))))

    # ---------- Spiele-Bibliotheken (Steam, Heroic) ----------

    def ensure_libraries(self, users: set[str]) -> None:
        """Für alle zugeordneten Kinder (unabhängig vom Programm-Modus) und jeden installierten
        Launcher: Bibliothek unter ``root/<user>``, die Ordner im Home als Verweise dorthin. Bereits
        vorhandene Ordner werden nach der Abmeldung verschoben – sonst starten die Spiele bei
        noexec-Home nicht."""
        self.lib_pending = {x for x in self.lib_pending if x[0] in users}
        self.lib_problems = {k: v for k, v in self.lib_problems.items() if k[1] in users}
        for library in libraries():
            if library.installed.exists():
                self._ensure(library, users)

    def _ensure(self, library: Library, users: set[str]) -> None:
        library.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        for user in users:
            home = safe_home(user)
            if home is None:
                continue
            pw = pwd.getpwnam(user)
            lib = library.root / user
            if not lib.exists():
                lib.mkdir(mode=0o700)
            st = os.lstat(lib)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                log.error("%s ist kein Verzeichnis – %s-Bibliothek übersprungen", lib, library.label)
                continue
            if st.st_uid != pw.pw_uid:
                os.chown(lib, pw.pw_uid, pw.pw_gid)
            problems = []
            for rel, sub in library.links:
                target = lib / sub if sub else lib
                r = self._link(pw, home, rel, target)
                if r.returncode == 3 and rel in library.caches:
                    r = self._link(pw, home, rel, target, replace=True)
                    if r.returncode == 0:
                        log.info("Cache ~/%s von %s gelöscht, Verweis nach %s angelegt", rel, user, target)
                if r.returncode == 3:
                    r, msg = self._migrate(user, pw, home, rel, lib, sub, library.label)
                    if msg:
                        problems.append(msg)
                if r.returncode == 0:
                    self.lib_pending.discard((user, rel))
                elif r.returncode != 3:
                    log.error("%s-Verweis %s für %s fehlgeschlagen: %s", library.label, rel, user, r.stderr.strip())
            key = (library.key, user)
            if problems:
                if self.lib_problems.get(key) != "; ".join(problems):
                    log.warning("%s: %s", user, "; ".join(problems))
                self.lib_problems[key] = "; ".join(problems)
            else:
                self.lib_problems.pop(key, None)

    def _link(self, pw, home: str, rel: str, target: Path, replace: bool = False) -> subprocess.CompletedProcess:
        """Link als Kind anlegen (nie als root in Kinder-Pfaden schreiben). 3 = anderes Ziel vorhanden.
        ``replace``: vorhandenen Ordner/Verweis vorher löschen (nur für Caches; als Kind)."""
        return self.run("setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--init-groups",
                         "--reset-env", "--", "/bin/sh", "-c",
                         'l="$1/$2"; mkdir -p "$(dirname "$l")" "$3" || exit 2; '
                         'if [ "$4" = 1 ] && [ "$(readlink "$l")" != "$3" ]; then rm -rf -- "$l" || exit 2; fi; '
                         'if [ -L "$l" ]; then [ "$(readlink "$l")" = "$3" ] && exit 0; exit 3; fi; '
                         '[ -e "$l" ] && exit 3; ln -s "$3" "$l"', "sh", home, rel, str(target), "1" if replace else "0")

    def _migrate(self, user: str, pw, home: str, rel: str, lib: Path, sub: str,
                 label: str) -> tuple[subprocess.CompletedProcess, str]:
        """Vorhandenen Ordner aus dem Home in die Bibliothek verschieben (nur abgemeldet, nur per
        Umbenennen auf derselben Partition). Rückgabe wie ``_link`` plus Problem für die Integrität."""
        fail = subprocess.CompletedProcess((), 3, "", "")
        target = lib / sub if sub else lib
        where = f"~/{rel}"
        if self.in_use(home):
            if (user, rel) not in self.lib_pending:
                log.warning("%s von %s liegt im Home – wird nach der Abmeldung nach %s verschoben",
                            where, user, target)
            self.lib_pending.add((user, rel))
            return fail, (f"{where} liegt im Home – wird nach der Abmeldung nach {target} verschoben "
                          f"(bis dahin startet {label} bei Programmkontrolle nicht)")
        self.lib_pending.discard((user, rel))
        try:
            moved = move_dir(home, pw.pw_uid, rel, lib, sub)
        except OSError as e:
            if e.errno == errno.EXDEV:
                # andere Partition/btrfs-Subvolume oder noexec-Home (Bind-Mount) aktiv
                return fail, (f"{where} liegt im Home und lässt sich nicht nach {target} umbenennen (andere "
                              "Partition oder Programmkontrolle aktiv) – bitte von Hand verschieben (siehe Anleitung)")
            if e.errno == errno.ENOTEMPTY:
                return fail, f"{where} liegt im Home, {target} ist aber nicht leer – bitte von Hand zusammenführen"
            return fail, f"{where} im Home nicht verschiebbar: {e.strerror}"
        if not moved:  # z. B. Verweis auf ein anderes Ziel
            return fail, f"{home}/{rel} ist kein Ordner und zeigt nicht auf {target} – bitte prüfen"
        log.info("%s von %s nach %s verschoben", where, user, target)
        return self._link(pw, home, rel, target), ""
