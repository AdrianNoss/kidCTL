"""Ausführverbot per NTFS-ACL (Ersatz für ``noexec``) für Kinder mit Programmkontrolle.

Auf dem Profil des Kindes (und auf ``C:\\Users\\Public`` und ``%WINDIR%\\Temp``, wo es ebenfalls schreiben darf):
- ``Deny <Kind>: (OI)(IO)(X)`` – Dateien darunter sind für das Kind nicht ausführbar; Windows lädt auch keine
  DLLs daraus (schützt vor COM-Hijacking über ``HKCU\\Software\\Classes``).
- ``Deny <Kind>: (OI)(CI)(WDAC)`` und ``OWNER RIGHTS: (OI)(CI)(M)`` – das Kind kann die Rechte seiner Dateien
  nicht ändern (sonst könnte es als Besitzer das Verbot an einer Datei abschalten).
- Freigegebene Apps im Profil: ``Allow <Kind>: (OI)(IO)(RX)`` auf ihrem App-Ordner. Vererbte Einträge des näheren
  Ordners gehen vor, das Verbot vom Profil greift dort nicht.
Geschrieben wird mit ``icacls`` (setzt auch die Vererbung auf vorhandene Dateien), gelesen über SDDL
(sprachunabhängig). Der App-Ordner liegt im Profil, das Kind kontrolliert also den Pfad: Damit eine Junction darin
nicht Rechte auf fremde Ordner bringt, bleiben alle Pfadteile während ``icacls`` geöffnet und gesperrt (``pin_path``).
"""

from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from ctypes import POINTER, byref, c_void_p, c_wchar_p
from ctypes import wintypes as w

from . import api

log = logging.getLogger("kidctl.acl")

OWNER_RIGHTS = "S-1-3-4"
WINDIR = os.environ.get("WINDIR", r"C:\Windows")
SHARED_DIRS = (os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public")), os.path.join(WINDIR, "Temp"))
DACL_SECURITY_INFORMATION = 0x4
SE_FILE_OBJECT = 1


def icacls(*args: str, run=subprocess.run) -> bool:
    r = run(["icacls", *args, "/C", "/Q"], capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        log.warning("icacls %s: %s", " ".join(args), r.stdout.decode("cp850", errors="replace").strip()[-300:])
    return r.returncode == 0


def protect(path: str, sid: str, run=subprocess.run) -> bool:
    ok = icacls(path, "/deny", f"*{sid}:(OI)(IO)(X)", f"*{sid}:(OI)(CI)(WDAC)", run=run)
    return icacls(path, "/grant", f"*{OWNER_RIGHTS}:(OI)(CI)(M)", run=run) and ok


def unprotect(path: str, sid: str, keep_owner_rights: bool = False, profile: bool = False,
              run=subprocess.run) -> bool:
    ok = icacls(path, "/remove:d", f"*{sid}", run=run)
    if profile:
        # icacls /deny … (WDAC) hat das Recht auch aus dem Vollzugriff des Kindes auf sein Profil gestrichen
        ok = icacls(path, "/grant", f"*{sid}:(OI)(CI)(WDAC)", run=run) and ok
    if not keep_owner_rights:
        ok = icacls(path, "/remove:g", f"*{OWNER_RIGHTS}", run=run) and ok
    return ok


def allow_dir(path: str, sid: str, run=subprocess.run) -> bool:
    return icacls(path, "/grant", f"*{sid}:(OI)(IO)(RX)", run=run)


def disallow_dir(path: str, sid: str, run=subprocess.run) -> bool:
    return icacls(path, "/remove:g", f"*{sid}", run=run)


@contextmanager
def pin_path(path: str, root: str):
    """Liefert ``True``, wenn ``path`` unterhalb von ``root`` ohne Junction/Symlink erreichbar ist, und hält alle
    Pfadteile (von oben nach unten) für die Dauer des Blocks gegen Umbenennen und Ersetzen fest."""
    handles, safe, current = [], True, root
    try:
        for part in ntpath.relpath(path, root).split("\\"):
            if part in ("", ".", ".."):
                safe = False
                break
            current = ntpath.join(current, part)
            handles.append(api.open_dir_pinned(current))
            if os.lstat(current).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                safe = False
                break
    except (OSError, ValueError) as e:
        log.warning("Pfad %s nicht sicher zu öffnen: %s", path, e)
        safe = False
    try:
        yield safe
    finally:
        for h in reversed(handles):
            api.close_handle(h)


@contextmanager
def _unpinned(_path: str, _root: str):
    yield True   # nur außerhalb von Windows (Tests)


def sddl(path: str) -> str:
    """DACL eines Ordners als SDDL-Text."""
    get = api._fn("advapi32", "GetNamedSecurityInfoW", w.DWORD, c_wchar_p, ctypes.c_int, w.DWORD, c_void_p,
                  c_void_p, POINTER(c_void_p), c_void_p, POINTER(c_void_p))
    dacl, sd = c_void_p(), c_void_p()
    rc = get(path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None, byref(dacl), None, byref(sd))
    if rc != 0:
        raise api._err("GetNamedSecurityInfo", rc)
    try:
        conv = api._fn("advapi32", "ConvertSecurityDescriptorToStringSecurityDescriptorW", w.BOOL, c_void_p,
                       w.DWORD, w.DWORD, POINTER(c_wchar_p), POINTER(w.ULONG))
        s = c_wchar_p()
        if not conv(sd, 1, DACL_SECURITY_INFORMATION, byref(s), None):
            raise api._err("ConvertSecurityDescriptorToStringSecurityDescriptor")
        try:
            return s.value
        finally:
            api._local_free(ctypes.cast(s, c_void_p))
    finally:
        api._local_free(sd)


def protected(text: str, sid: str) -> bool:
    """Verbot (Ausführen, Rechte ändern) für ``sid`` im SDDL-Text vorhanden?"""
    aces = re.findall(r"\((D);([A-Z]*);([^;]*);;;([^)]*)\)", text)
    mine = [(flags, rights) for _t, flags, rights, who in aces if who == sid]
    exec_deny = any("OI" in f and "IO" in f and r for f, r in mine)
    wdac_deny = any("OI" in f and "CI" in f and "IO" not in f for f, _r in mine)
    return exec_deny and wdac_deny


def profile_path(sid: str) -> str | None:
    """Profilordner eines Kontos (erst nach der ersten Anmeldung vorhanden)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\{sid}") as k:
            path, _typ = winreg.QueryValueEx(k, "ProfileImagePath")
    except OSError:
        return None
    path = os.path.expandvars(str(path))
    return path if os.path.isdir(path) else None


def _under(path: str, folder: str) -> bool:
    p, f = os.path.normcase(path), os.path.normcase(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


class AclManager:
    """Hält die Verbote an Profilen, gemeinsamen Ordnern und die Ausnahmen für App-Ordner aktuell
    (Zustand in ``winacl.json``, damit nur selbst gesetzte Einträge zurückgenommen werden)."""

    STATE = "winacl.json"

    def __init__(self, store_mod, run=subprocess.run, profile=profile_path, shared=SHARED_DIRS, pin=None) -> None:
        self.store = store_mod
        self.pin = pin or (pin_path if sys.platform == "win32" else _unpinned)
        self.run = run
        self.profile = profile
        self.shared_dirs = tuple(d for d in shared if d)
        data = store_mod.load(self.STATE) or {}
        self.profiles: dict[str, str] = data.get("profiles", {})
        self.shared: dict[str, list[str]] = data.get("shared", {})
        self.dirs: dict[str, list[str]] = data.get("dirs", {})

    def _save(self) -> None:
        self.store.save(self.STATE, {"profiles": self.profiles, "shared": self.shared, "dirs": self.dirs})

    def sync(self, wanted: dict[str, list[str]], extra: dict[str, list[str]] | None = None) -> None:
        """``wanted``: SID -> freigegebene App-Ordner. Kinder ohne Programmkontrolle fehlen. ``extra``: SID ->
        weitere vom Kind beschreibbare Ordner mit Verbot (z. B. Steam-Bibliothek ohne Steam-Freigabe)."""
        extra = extra or {}
        for path, sids in list(self.shared.items()):   # Ordner, die für ein Kind nicht mehr gelten sollen
            for sid in list(sids):
                if sid in wanted and path not in self.shared_dirs and path not in extra.get(sid, []):
                    sids.remove(sid)
                    unprotect(path, sid, keep_owner_rights=bool(sids), run=self.run)
                    log.info("Ausführverbot in %s für %s aufgehoben", path, sid)
        for sid, dirs in wanted.items():
            path = self.profile(sid)
            if path and self.profiles.get(sid) != path and protect(path, sid, run=self.run):
                self.profiles[sid] = path
                log.warning("Ausführverbot im Profil %s gesetzt", path)
            for d in list(self.shared_dirs) + [x for x in extra.get(sid, []) if x not in self.shared_dirs]:
                if sid not in self.shared.setdefault(d, []) and os.path.isdir(d) and protect(d, sid, run=self.run):
                    self.shared[d].append(sid)
            want = sorted(x for x in dirs if path and _under(x, path) and os.path.isdir(x))
            have = self.dirs.get(sid, [])
            for d in list(want):
                if d in have:
                    continue
                with self.pin(d, path) as safe:
                    if not safe:
                        want.remove(d)   # beim nächsten Abgleich erneut versuchen
                        log.warning("App-Ordner %s nicht freigegeben: Pfad enthält eine Verknüpfung oder ist in Benutzung", d)
                    elif allow_dir(d, sid, run=self.run):
                        log.info("App-Ordner %s ausführbar (Freigabe)", d)
            for d in have:
                if d not in want and path and os.path.isdir(d):
                    self._revoke(d, sid, path)
            self.dirs[sid] = want
        for sid in [s for s in list(self.profiles) + [s for v in self.shared.values() for s in v] if s not in wanted]:
            root = self.profiles.get(sid) or self.profile(sid)
            if sid in self.profiles:
                unprotect(self.profiles.pop(sid), sid, profile=True, run=self.run)
                log.warning("Ausführverbot im Profil von %s aufgehoben", sid)
            for d, sids in self.shared.items():
                if sid in sids:
                    sids.remove(sid)
                    unprotect(d, sid, keep_owner_rights=bool(sids), run=self.run)
            for d in self.dirs.pop(sid, []):
                if root:
                    self._revoke(d, sid, root)
        self._save()

    def _revoke(self, d: str, sid: str, root: str) -> None:
        with self.pin(d, root) as safe:
            if safe:
                disallow_dir(d, sid, run=self.run)

    def forget(self, sid: str) -> None:
        """Beim nächsten Abgleich neu setzen (Integrität verletzt)."""
        self.profiles.pop(sid, None)
        for sids in self.shared.values():
            if sid in sids:
                sids.remove(sid)
        self.dirs.pop(sid, None)

    def problems(self, wanted: dict[str, str], read=sddl) -> dict[str, str]:
        """SID -> Profilpfad, wo das Verbot fehlt (``wanted``: SID -> User)."""
        out = {}
        for sid in wanted:
            path = self.profiles.get(sid) or self.profile(sid)
            if not path:
                continue   # noch nie angemeldet
            try:
                if not protected(read(path), sid):
                    out[sid] = path
            except OSError:
                out[sid] = path
        return out
