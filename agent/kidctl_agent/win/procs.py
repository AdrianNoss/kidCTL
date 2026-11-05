"""Prozesse unter Windows: Liste (Toolhelp), Details eines Prozesses, Beenden, Besitzer einer Datei.

Ersatz für Netlink ``PROC_EVENTS``: Die Liste wird alle 0,5 s gelesen (wenige hundert Prozesse, < 1 ms); neue
PIDs werden sofort geprüft. Kurzlebige Prozesse können durchrutschen – eigener Code des Kindes kann wegen des
Ausführverbots (ACL) trotzdem nicht laufen.
"""

from __future__ import annotations

import ctypes
import functools
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
            out[e.th32ProcessID] = e.th32ParentProcessID
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
