"""Prozesse unter Windows: Liste (Toolhelp), Details eines Prozesses, Beenden, Besitzer einer Datei.

Ersatz für Netlink ``PROC_EVENTS``: Die Liste wird alle 0,5 s gelesen (wenige hundert Prozesse, < 1 ms); neue
PIDs werden sofort geprüft. Kurzlebige Prozesse können durchrutschen – eigener Code des Kindes kann wegen des
Ausführverbots (ACL) trotzdem nicht laufen.
"""

from __future__ import annotations

import ctypes
import functools
import ntpath
import os
import time
from ctypes import POINTER, Structure, byref, c_void_p, c_wchar_p, sizeof
from ctypes import wintypes as w
from dataclasses import dataclass

from . import api

TH32CS_SNAPPROCESS = 0x2
PROCESS_TERMINATE = 0x1
ProcessCommandLineInformation = 60
APPMODEL_ERROR_NO_PACKAGE = 15700
SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x1


class PROCESSENTRY32W(Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                ("szExeFile", w.WCHAR * 260)]


class UNICODE_STRING(Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", c_void_p)]


@dataclass(frozen=True)
class WinProc:
    pid: int
    ppid: int
    exe: str            # voller Pfad (Laufwerksbuchstabe)
    session: int
    sid: str            # Benutzer des Prozesses
    start: int          # Startzeit (FILETIME) – mit der PID eindeutig
    cmdline: str = ""
    pfn: str = ""       # Package Family Name (Store-App), sonst leer

    @property
    def ident(self) -> tuple[int, int]:
        return (self.pid, self.start)


def _k32(name, restype, *argtypes):
    return api._fn("kernel32", name, restype, *argtypes)


def snapshot() -> dict[int, int]:
    """PID -> Eltern-PID aller Prozesse."""
    return {pid: ppid for pid, (ppid, _name) in snapshot_names().items()}


def snapshot_names() -> dict[int, tuple[int, str]]:
    """PID -> (Eltern-PID, Dateiname) aller Prozesse (für die Abstammung von Spielen)."""
    h = _k32("CreateToolhelp32Snapshot", w.HANDLE, w.DWORD, w.DWORD)(TH32CS_SNAPPROCESS, 0)
    if h is None or h == api.INVALID_HANDLE_VALUE:
        raise api._err("CreateToolhelp32Snapshot")
    try:
        first = _k32("Process32FirstW", w.BOOL, w.HANDLE, POINTER(PROCESSENTRY32W))
        nxt = _k32("Process32NextW", w.BOOL, w.HANDLE, POINTER(PROCESSENTRY32W))
        e = PROCESSENTRY32W(dwSize=sizeof(PROCESSENTRY32W))
        out = {}
        ok = first(h, byref(e))
        while ok:
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = nxt(h, byref(e))
        return out
    finally:
        api.close_handle(h)


def _image(h) -> str:
    buf = ctypes.create_unicode_buffer(32768)
    size = w.DWORD(len(buf))
    if not _k32("QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD, c_wchar_p, POINTER(w.DWORD))(
            h, 0, buf, byref(size)):
        raise api._err("QueryFullProcessImageName")
    return buf.value


def _start_time(h) -> int:
    c, e, k, u = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
    if not _k32("GetProcessTimes", w.BOOL, w.HANDLE, *(POINTER(w.FILETIME),) * 4)(
            h, byref(c), byref(e), byref(k), byref(u)):
        raise api._err("GetProcessTimes")
    return (c.dwHighDateTime << 32) | c.dwLowDateTime


def _cmdline(h) -> str:
    f = api._fn("ntdll", "NtQueryInformationProcess", ctypes.c_long, w.HANDLE, ctypes.c_int, c_void_p, w.ULONG,
                POINTER(w.ULONG))
    size = w.ULONG(0)
    f(h, ProcessCommandLineInformation, None, 0, byref(size))
    if not size.value:
        return ""
    buf = ctypes.create_string_buffer(size.value)
    if f(h, ProcessCommandLineInformation, buf, size.value, byref(size)) != 0:
        return ""
    us = ctypes.cast(buf, POINTER(UNICODE_STRING)).contents
    return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""


def _pfn(h) -> str:
    f = _k32("GetPackageFamilyName", ctypes.c_long, w.HANDLE, POINTER(w.UINT), c_wchar_p)
    size = w.UINT(0)
    rc = f(h, byref(size), None)
    if rc == APPMODEL_ERROR_NO_PACKAGE or not size.value:
        return ""
    buf = ctypes.create_unicode_buffer(size.value)
    return buf.value if f(h, byref(size), buf) == 0 else ""


def _session(pid: int) -> int:
    s = w.DWORD()
    return s.value if _k32("ProcessIdToSessionId", w.BOOL, w.DWORD, POINTER(w.DWORD))(pid, byref(s)) else -1


def read(pid: int, ppid: int = 0) -> WinProc | None:
    """Details eines Prozesses; ``None``, wenn er beendet oder nicht lesbar ist (z. B. geschützter Prozess)."""
    h = _k32("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        try:
            exe, start = _image(h), _start_time(h)
        except OSError:
            return None
        try:
            sid = api.process_user_sid(pid)
        except OSError:
            return None
        return WinProc(pid=pid, ppid=ppid, exe=exe, session=_session(pid), sid=sid, start=start,
                       cmdline=_cmdline(h), pfn=_pfn(h))
    finally:
        api.close_handle(h)


def terminate(pid: int) -> bool:
    h = _k32("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(_k32("TerminateProcess", w.BOOL, w.HANDLE, w.UINT)(h, 1))
    finally:
        api.close_handle(h)


@functools.lru_cache(maxsize=4096)
def _owner_cached(path: str, mtime: int) -> str:
    f = api._fn("advapi32", "GetNamedSecurityInfoW", w.DWORD, c_wchar_p, ctypes.c_int, w.DWORD,
                POINTER(c_void_p), c_void_p, c_void_p, c_void_p, POINTER(c_void_p))
    owner, sd = c_void_p(), c_void_p()
    rc = f(path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION, byref(owner), None, None, None, byref(sd))
    if rc != 0:
        raise api._err("GetNamedSecurityInfo", rc)
    try:
        return api.sid_to_string(owner)
    finally:
        api._local_free(sd)


def file_owner(path: str) -> str:
    """SID des Besitzers einer Datei (zwischengespeichert je Pfad und Änderungszeit)."""
    import os
    return _owner_cached(path, int(os.stat(path).st_mtime_ns))


# ---------- Wechseldatenträger ----------

DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_CDROM = 2, 3, 5
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
# STORAGE_BUS_TYPE: 1394, USB, SD, MMC, eingehängte Abbilder (ISO/VHD). USB-Festplatten melden sich oft als
# DRIVE_FIXED – erkennbar am Bus. Thunderbolt-NVMe meldet „NVMe“ und ist nicht unterscheidbar (Restlücke).
REMOVABLE_BUSES = {0x4, 0x7, 0xC, 0xD, 0xF}
REMOVABLE_TTL = 10.0   # Laufwerksbuchstaben wechseln beim Umstecken
_removable_cache: dict[str, tuple[float, bool]] = {}


def descriptor_removable(raw: bytes) -> bool:
    """``STORAGE_DEVICE_DESCRIPTOR`` -> Wechseldatenträger? (``RemovableMedia`` an Byte 10, ``BusType`` ab Byte 28)"""
    if len(raw) < 32:
        return False
    return bool(raw[10]) or int.from_bytes(raw[28:32], "little") in REMOVABLE_BUSES


def _query_removable(letter: str) -> bool:
    kind = api._fn("kernel32", "GetDriveTypeW", w.UINT, c_wchar_p)(f"{letter}:\\")
    if kind in (DRIVE_REMOVABLE, DRIVE_CDROM):
        return True
    if kind != DRIVE_FIXED:
        return False   # Netzlaufwerk, unbekannt
    create = api._fn("kernel32", "CreateFileW", w.HANDLE, c_wchar_p, w.DWORD, w.DWORD, c_void_p, w.DWORD, w.DWORD,
                     w.HANDLE)
    h = create(f"\\\\.\\{letter}:", 0, 0x1 | 0x2, None, 3, 0, None)   # ohne Zugriffsrechte, OPEN_EXISTING
    if h in (None, api.INVALID_HANDLE_VALUE):
        return False
    try:
        query = (w.DWORD * 3)(0, 0, 0)   # StorageDeviceProperty, PropertyStandardQuery
        buf = ctypes.create_string_buffer(1024)
        got = w.DWORD()
        ioctl = api._fn("kernel32", "DeviceIoControl", w.BOOL, w.HANDLE, w.DWORD, c_void_p, w.DWORD, c_void_p,
                        w.DWORD, POINTER(w.DWORD), c_void_p)
        if not ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, byref(query), sizeof(query), buf, sizeof(buf), byref(got), None):
            return False
        return descriptor_removable(buf.raw[:got.value])
    finally:
        api.close_handle(h)


def on_removable(path: str, query=_query_removable, now=None) -> bool:
    """Liegt ``path`` auf einem Wechseldatenträger (USB-Stick, USB-Festplatte, SD-Karte, DVD, eingehängtes Abbild)?
    Das Systemlaufwerk nie; bei Fehlern ``False`` (wie bisher)."""
    drive = ntpath.splitdrive(path)[0]
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
        return False
    letter = drive[0].upper()
    if letter == os.environ.get("SystemDrive", "C:")[:1].upper():
        return False
    t = time.monotonic() if now is None else now
    hit = _removable_cache.get(letter)
    if hit and t - hit[0] < REMOVABLE_TTL:
        return hit[1]
    try:
        value = bool(query(letter))
    except OSError:
        value = False
    _removable_cache[letter] = (t, value)
    return value
