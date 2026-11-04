"""Dünne ctypes-Schicht über die benötigten Win32-Funktionen (WTS, NetUser, Token/SID, Named Pipes, Dienste).

Alle Funktionen werfen ``OSError`` (mit Windows-Fehlercode) bei Fehlschlag. Die DLLs werden erst beim ersten
Aufruf geladen.
"""

from __future__ import annotations

import ctypes
import functools
from ctypes import POINTER, Structure, Union, byref, c_int, c_long, c_ulong, c_void_p, c_wchar_p, sizeof
from ctypes import wintypes as w

# ---------- Konstanten ----------

WTS_CURRENT_SERVER_HANDLE = None
# WTS_INFO_CLASS
WTSUserName, WTSDomainName, WTSConnectState, WTSSessionInfoEx = 5, 7, 8, 25
# WTS_CONNECTSTATE_CLASS
WTSActive, WTSConnected, WTSDisconnected = 0, 1, 4
WTS_SESSIONSTATE_LOCK, WTS_SESSIONSTATE_UNLOCK = 0, 1
MB_OK, MB_ICONWARNING, MB_SYSTEMMODAL, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x30, 0x1000, 0x10000, 0x40000

FILTER_NORMAL_ACCOUNT = 0x2
UF_ACCOUNTDISABLE = 0x2
LG_INCLUDE_INDIRECT = 0x1
MAX_PREFERRED_LENGTH = 0xFFFFFFFF
NERR_Success = 0
ERROR_MORE_DATA = 234

ERROR_IO_PENDING = 997
ERROR_PIPE_CONNECTED = 535
ERROR_BROKEN_PIPE = 109
ERROR_OPERATION_ABORTED = 995
ERROR_NO_DATA = 232
PIPE_ACCESS_DUPLEX = 0x3
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x80000
PIPE_TYPE_BYTE, PIPE_READMODE_BYTE, PIPE_WAIT, PIPE_REJECT_REMOTE_CLIENTS = 0x0, 0x0, 0x0, 0x8
PIPE_UNLIMITED_INSTANCES = 255
INVALID_HANDLE_VALUE = c_void_p(-1).value
WAIT_OBJECT_0, WAIT_TIMEOUT, INFINITE = 0, 0x102, 0xFFFFFFFF
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TokenUser = 1
SDDL_REVISION_1 = 1

# Dienst
SERVICE_WIN32_OWN_PROCESS = 0x10
SERVICE_STOPPED, SERVICE_START_PENDING, SERVICE_STOP_PENDING, SERVICE_RUNNING = 1, 2, 3, 4
SERVICE_CONTROL_STOP, SERVICE_CONTROL_INTERROGATE, SERVICE_CONTROL_SHUTDOWN = 1, 4, 5
SERVICE_ACCEPT_STOP, SERVICE_ACCEPT_SHUTDOWN = 0x1, 0x4
NO_ERROR = 0

# ---------- Strukturen ----------


class WTS_SESSION_INFOW(Structure):
    _fields_ = [("SessionId", w.DWORD), ("pWinStationName", c_wchar_p), ("State", c_int)]


class WTSINFOEX_LEVEL1_W(Structure):
    _fields_ = [("SessionId", c_ulong), ("SessionState", c_int), ("SessionFlags", c_long),
                ("WinStationName", w.WCHAR * 33), ("UserName", w.WCHAR * 21), ("DomainName", w.WCHAR * 18),
                ("LogonTime", ctypes.c_longlong), ("ConnectTime", ctypes.c_longlong),
                ("DisconnectTime", ctypes.c_longlong), ("LastInputTime", ctypes.c_longlong),
                ("CurrentTime", ctypes.c_longlong), ("IncomingBytes", w.DWORD), ("OutgoingBytes", w.DWORD),
                ("IncomingFrames", w.DWORD), ("OutgoingFrames", w.DWORD),
                ("IncomingCompressedBytes", w.DWORD), ("OutgoingCompressedBytes", w.DWORD)]


class _WTSINFOEX_LEVEL_W(Union):
    _fields_ = [("WTSInfoExLevel1", WTSINFOEX_LEVEL1_W)]


class WTSINFOEXW(Structure):
    _fields_ = [("Level", w.DWORD), ("Data", _WTSINFOEX_LEVEL_W)]


class USER_INFO_20(Structure):
    _fields_ = [("name", c_wchar_p), ("full_name", c_wchar_p), ("comment", c_wchar_p),
                ("flags", w.DWORD), ("user_id", w.DWORD)]


class USER_INFO_24(Structure):
    _fields_ = [("internet_identity", w.BOOL), ("flags", w.DWORD), ("internet_provider_name", c_wchar_p),
                ("internet_principal_name", c_wchar_p), ("user_sid", c_void_p)]


class USER_INFO_1008(Structure):
    _fields_ = [("flags", w.DWORD)]


class LOCALGROUP_USERS_INFO_0(Structure):
    _fields_ = [("name", c_wchar_p)]


class SECURITY_ATTRIBUTES(Structure):
    _fields_ = [("nLength", w.DWORD), ("lpSecurityDescriptor", c_void_p), ("bInheritHandle", w.BOOL)]


class OVERLAPPED(Structure):
    _fields_ = [("Internal", c_void_p), ("InternalHigh", c_void_p), ("Offset", w.DWORD),
                ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


class SID_AND_ATTRIBUTES(Structure):
    _fields_ = [("Sid", c_void_p), ("Attributes", w.DWORD)]


class SERVICE_STATUS(Structure):
    _fields_ = [("dwServiceType", w.DWORD), ("dwCurrentState", w.DWORD), ("dwControlsAccepted", w.DWORD),
                ("dwWin32ExitCode", w.DWORD), ("dwServiceSpecificExitCode", w.DWORD),
                ("dwCheckPoint", w.DWORD), ("dwWaitHint", w.DWORD)]


# ---------- DLLs (verzögert geladen) ----------

@functools.cache
def _dll(name: str):
    return ctypes.WinDLL(name, use_last_error=True)


def _fn(dll: str, name: str, restype, *argtypes):
    f = getattr(_dll(dll), name)
    f.restype, f.argtypes = restype, list(argtypes)
    return f


def _err(what: str, code: int | None = None) -> OSError:
    code = ctypes.get_last_error() if code is None else code
    return OSError(code, f"{what}: {ctypes.FormatError(code).strip()} ({code})")


# ---------- WTS (Sitzungen) ----------

def enumerate_sessions() -> list[tuple[int, int]]:
    """[(SessionId, Verbindungszustand)]"""
    f = _fn("wtsapi32", "WTSEnumerateSessionsW", w.BOOL, w.HANDLE, w.DWORD, w.DWORD,
            POINTER(POINTER(WTS_SESSION_INFOW)), POINTER(w.DWORD))
    buf, count = POINTER(WTS_SESSION_INFOW)(), w.DWORD()
    if not f(WTS_CURRENT_SERVER_HANDLE, 0, 1, byref(buf), byref(count)):
        raise _err("WTSEnumerateSessions")
    try:
        return [(buf[i].SessionId, buf[i].State) for i in range(count.value)]
    finally:
        wts_free(buf)


def wts_free(ptr) -> None:
    _fn("wtsapi32", "WTSFreeMemory", None, c_void_p)(ctypes.cast(ptr, c_void_p))


def _query(session_id: int, info_class: int) -> tuple[c_void_p, int]:
    f = _fn("wtsapi32", "WTSQuerySessionInformationW", w.BOOL, w.HANDLE, w.DWORD, c_int,
            POINTER(c_void_p), POINTER(w.DWORD))
    buf, size = c_void_p(), w.DWORD()
    if not f(WTS_CURRENT_SERVER_HANDLE, session_id, info_class, byref(buf), byref(size)):
        raise _err("WTSQuerySessionInformation")
    return buf, size.value


def session_info(session_id: int) -> WTSINFOEX_LEVEL1_W:
    """Benutzer, Zustand und Sperre einer Sitzung (``SessionFlags``: 0 gesperrt, 1 entsperrt, -1 unbekannt)."""
    buf, _size = _query(session_id, WTSSessionInfoEx)
    try:
        info = ctypes.cast(buf, POINTER(WTSINFOEXW)).contents.Data.WTSInfoExLevel1
        out = WTSINFOEX_LEVEL1_W()
        ctypes.memmove(byref(out), byref(info), sizeof(out))
        return out
    finally:
        wts_free(buf)


def session_string(session_id: int, info_class: int) -> str:
    buf, _size = _query(session_id, info_class)
    try:
        return ctypes.wstring_at(buf.value) if buf.value else ""
    finally:
        wts_free(buf)


def logoff_session(session_id: int, wait: bool = False) -> None:
    f = _fn("wtsapi32", "WTSLogoffSession", w.BOOL, w.HANDLE, w.DWORD, w.BOOL)
    if not f(WTS_CURRENT_SERVER_HANDLE, session_id, wait):
        raise _err("WTSLogoffSession")


def send_message(session_id: int, title: str, text: str, timeout: int = 60) -> int:
    """Meldungsfenster in einer Sitzung (aus dem Dienst heraus); wartet bis zum Klick oder ``timeout`` s."""
    f = _fn("wtsapi32", "WTSSendMessageW", w.BOOL, w.HANDLE, w.DWORD, c_wchar_p, w.DWORD, c_wchar_p, w.DWORD,
            w.DWORD, w.DWORD, POINTER(w.DWORD), w.BOOL)
    resp = w.DWORD()
    style = MB_OK | MB_ICONWARNING | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
    if not f(WTS_CURRENT_SERVER_HANDLE, session_id, title, len(title) * 2, text, len(text) * 2, style,
             timeout, byref(resp), True):
        raise _err("WTSSendMessage")
    return resp.value


# ---------- Benutzerkonten (NetApi) ----------

def _net_check(what: str, status: int) -> None:
    if status != NERR_Success:
        raise _err(what, status)


def net_free(ptr) -> None:
    _fn("netapi32", "NetApiBufferFree", w.DWORD, c_void_p)(ctypes.cast(ptr, c_void_p))


def user_enum() -> list[tuple[str, str, int, int]]:
    """Lokale Konten: [(Name, voller Name, RID, Flags)]."""
    f = _fn("netapi32", "NetUserEnum", w.DWORD, c_wchar_p, w.DWORD, w.DWORD, POINTER(c_void_p), w.DWORD,
            POINTER(w.DWORD), POINTER(w.DWORD), POINTER(w.DWORD))
    out, resume = [], w.DWORD(0)
    while True:
        buf, read, total = c_void_p(), w.DWORD(), w.DWORD()
        status = f(None, 20, FILTER_NORMAL_ACCOUNT, byref(buf), MAX_PREFERRED_LENGTH, byref(read), byref(total),
                   byref(resume))
        if status not in (NERR_Success, ERROR_MORE_DATA):
            raise _err("NetUserEnum", status)
        try:
            arr = ctypes.cast(buf, POINTER(USER_INFO_20))
            out += [(arr[i].name, arr[i].full_name or "", arr[i].user_id, arr[i].flags) for i in range(read.value)]
        finally:
            if buf:
                net_free(buf)
        if status != ERROR_MORE_DATA:
            return out


def user_flags(name: str) -> int:
    f = _fn("netapi32", "NetUserGetInfo", w.DWORD, c_wchar_p, c_wchar_p, w.DWORD, POINTER(c_void_p))
    buf = c_void_p()
    _net_check("NetUserGetInfo", f(None, name, 20, byref(buf)))
    try:
        return ctypes.cast(buf, POINTER(USER_INFO_20)).contents.flags
    finally:
        net_free(buf)


def user_internet_name(name: str) -> str:
    """E-Mail-Adresse des verknüpften Microsoft-Kontos, sonst leer."""
    f = _fn("netapi32", "NetUserGetInfo", w.DWORD, c_wchar_p, c_wchar_p, w.DWORD, POINTER(c_void_p))
    buf = c_void_p()
    if f(None, name, 24, byref(buf)) != NERR_Success:
        return ""
    try:
        info = ctypes.cast(buf, POINTER(USER_INFO_24)).contents
        return (info.internet_principal_name or "") if info.internet_identity else ""
    finally:
        net_free(buf)


def set_user_flags(name: str, flags: int) -> None:
    f = _fn("netapi32", "NetUserSetInfo", w.DWORD, c_wchar_p, c_wchar_p, w.DWORD, c_void_p, POINTER(w.DWORD))
    info, parm = USER_INFO_1008(flags), w.DWORD()
    _net_check("NetUserSetInfo", f(None, name, 1008, byref(info), byref(parm)))


def user_local_groups(name: str) -> list[str]:
    """Lokale Gruppen des Kontos (auch indirekt)."""
    f = _fn("netapi32", "NetUserGetLocalGroups", w.DWORD, c_wchar_p, c_wchar_p, w.DWORD, w.DWORD,
            POINTER(c_void_p), w.DWORD, POINTER(w.DWORD), POINTER(w.DWORD))
    buf, read, total = c_void_p(), w.DWORD(), w.DWORD()
    _net_check("NetUserGetLocalGroups", f(None, name, 0, LG_INCLUDE_INDIRECT, byref(buf), MAX_PREFERRED_LENGTH,
                                          byref(read), byref(total)))
    try:
        arr = ctypes.cast(buf, POINTER(LOCALGROUP_USERS_INFO_0))
        return [arr[i].name for i in range(read.value)]
    finally:
        net_free(buf)


# ---------- SIDs ----------

def _local_free(ptr) -> None:
    _fn("kernel32", "LocalFree", c_void_p, c_void_p)(ptr)


def sid_to_string(psid) -> str:
    f = _fn("advapi32", "ConvertSidToStringSidW", w.BOOL, c_void_p, POINTER(c_wchar_p))
    s = c_wchar_p()
    if not f(psid, byref(s)):
        raise _err("ConvertSidToStringSid")
    try:
        return s.value
    finally:
        _local_free(ctypes.cast(s, c_void_p))


def account_name(sid: str) -> tuple[str, str]:
    """(Name, Domäne) zu einer SID (Gruppennamen sind lokalisiert, z. B. „Administratoren“)."""
    conv = _fn("advapi32", "ConvertStringSidToSidW", w.BOOL, c_wchar_p, POINTER(c_void_p))
    psid = c_void_p()
    if not conv(sid, byref(psid)):
        raise _err("ConvertStringSidToSid")
    try:
        f = _fn("advapi32", "LookupAccountSidW", w.BOOL, c_wchar_p, c_void_p, c_wchar_p, POINTER(w.DWORD),
                c_wchar_p, POINTER(w.DWORD), POINTER(c_int))
        name, dom = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        n, d, use = w.DWORD(256), w.DWORD(256), c_int()
        if not f(None, psid, name, byref(n), dom, byref(d), byref(use)):
            raise _err("LookupAccountSid")
        return name.value, dom.value
    finally:
        _local_free(psid)


def account_sid(name: str) -> str:
    f = _fn("advapi32", "LookupAccountNameW", w.BOOL, c_wchar_p, c_wchar_p, c_void_p, POINTER(w.DWORD),
            c_wchar_p, POINTER(w.DWORD), POINTER(c_int))
    sid, dom = ctypes.create_string_buffer(256), ctypes.create_unicode_buffer(256)
    n, d, use = w.DWORD(256), w.DWORD(256), c_int()
    if not f(None, name, sid, byref(n), dom, byref(d), byref(use)):
        raise _err("LookupAccountName")
    return sid_to_string(sid)


def process_user_sid(pid: int) -> str:
    """SID des Benutzers, dem der Prozess gehört (Token des Prozesses)."""
    k32 = "kernel32"
    open_proc = _fn(k32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    close = _fn(k32, "CloseHandle", w.BOOL, w.HANDLE)
    hproc = open_proc(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not hproc:
        raise _err("OpenProcess")
    try:
        token = w.HANDLE()
        if not _fn("advapi32", "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, POINTER(w.HANDLE))(
                hproc, TOKEN_QUERY, byref(token)):
            raise _err("OpenProcessToken")
        try:
            get = _fn("advapi32", "GetTokenInformation", w.BOOL, w.HANDLE, c_int, c_void_p, w.DWORD,
                      POINTER(w.DWORD))
            size = w.DWORD()
            get(token, TokenUser, None, 0, byref(size))
            buf = ctypes.create_string_buffer(size.value)
            if not get(token, TokenUser, buf, size, byref(size)):
                raise _err("GetTokenInformation")
            return sid_to_string(ctypes.cast(buf, POINTER(SID_AND_ATTRIBUTES)).contents.Sid)
        finally:
            close(token)
    finally:
        close(hproc)


# ---------- Named Pipes ----------

def security_attributes(sddl: str) -> tuple[SECURITY_ATTRIBUTES, c_void_p]:
    """SECURITY_ATTRIBUTES aus SDDL; der Deskriptor (zweiter Wert) bleibt so lange gültig, wie er lebt."""
    f = _fn("advapi32", "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL, c_wchar_p, w.DWORD,
            POINTER(c_void_p), POINTER(w.DWORD))
    psd = c_void_p()
    if not f(sddl, SDDL_REVISION_1, byref(psd), None):
        raise _err("ConvertStringSecurityDescriptorToSecurityDescriptor")
    return SECURITY_ATTRIBUTES(sizeof(SECURITY_ATTRIBUTES), psd, False), psd


def create_pipe(name: str, sa: SECURITY_ATTRIBUTES, first: bool) -> int:
    f = _fn("kernel32", "CreateNamedPipeW", w.HANDLE, c_wchar_p, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
            w.DWORD, POINTER(SECURITY_ATTRIBUTES))
    mode = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | (FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
    h = f(name, mode, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
          PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, byref(sa))
    if h is None or h == INVALID_HANDLE_VALUE:
        raise _err("CreateNamedPipe")
    return h


def create_event() -> int:
    h = _fn("kernel32", "CreateEventW", w.HANDLE, c_void_p, w.BOOL, w.BOOL, c_wchar_p)(None, True, False, None)
    if not h:
        raise _err("CreateEvent")
    return h


def set_event(h: int) -> None:
    _fn("kernel32", "SetEvent", w.BOOL, w.HANDLE)(h)


def reset_event(h: int) -> None:
    _fn("kernel32", "ResetEvent", w.BOOL, w.HANDLE)(h)


def close_handle(h: int) -> None:
    _fn("kernel32", "CloseHandle", w.BOOL, w.HANDLE)(h)


def wait_any(handles: list[int], timeout_ms: int = INFINITE) -> int:
    """Index des signalisierten Handles bzw. ``WAIT_TIMEOUT``."""
    arr = (w.HANDLE * len(handles))(*handles)
    r = _fn("kernel32", "WaitForMultipleObjects", w.DWORD, w.DWORD, POINTER(w.HANDLE), w.BOOL, w.DWORD)(
        len(handles), arr, False, timeout_ms)
    if r == WAIT_TIMEOUT:
        return WAIT_TIMEOUT
    if r >= len(handles):
        raise _err("WaitForMultipleObjects")
    return r - WAIT_OBJECT_0


def connect_pipe(h: int, ov: OVERLAPPED) -> bool:
    """True = schon verbunden, False = ausstehend (auf ``ov.hEvent`` warten)."""
    ok = _fn("kernel32", "ConnectNamedPipe", w.BOOL, w.HANDLE, POINTER(OVERLAPPED))(h, byref(ov))
    if ok:
        return True
    code = ctypes.get_last_error()
    if code == ERROR_PIPE_CONNECTED:
        return True
    if code == ERROR_IO_PENDING:
        return False
    raise _err("ConnectNamedPipe", code)


def disconnect_pipe(h: int) -> None:
    _fn("kernel32", "DisconnectNamedPipe", w.BOOL, w.HANDLE)(h)


def cancel_io(h: int) -> None:
    _fn("kernel32", "CancelIoEx", w.BOOL, w.HANDLE, c_void_p)(h, None)


def start_read(h: int, buf, ov: OVERLAPPED) -> None:
    ok = _fn("kernel32", "ReadFile", w.BOOL, w.HANDLE, c_void_p, w.DWORD, POINTER(w.DWORD), POINTER(OVERLAPPED))(
        h, buf, len(buf), None, byref(ov))
    if not ok and (code := ctypes.get_last_error()) != ERROR_IO_PENDING:
        raise _err("ReadFile", code)


def start_write(h: int, data: bytes, ov: OVERLAPPED) -> None:
    ok = _fn("kernel32", "WriteFile", w.BOOL, w.HANDLE, c_void_p, w.DWORD, POINTER(w.DWORD), POINTER(OVERLAPPED))(
        h, data, len(data), None, byref(ov))
    if not ok and (code := ctypes.get_last_error()) != ERROR_IO_PENDING:
        raise _err("WriteFile", code)


def overlapped_result(h: int, ov: OVERLAPPED) -> int:
    """Übertragene Bytes einer abgeschlossenen Operation (wirft bei Fehler, z. B. Gegenseite getrennt)."""
    n = w.DWORD()
    if not _fn("kernel32", "GetOverlappedResult", w.BOOL, w.HANDLE, POINTER(OVERLAPPED), POINTER(w.DWORD),
               w.BOOL)(h, byref(ov), byref(n), True):
        raise _err("GetOverlappedResult")
    return n.value


def pipe_client_pid(h: int) -> int:
    pid = c_ulong()
    if not _fn("kernel32", "GetNamedPipeClientProcessId", w.BOOL, w.HANDLE, POINTER(c_ulong))(h, byref(pid)):
        raise _err("GetNamedPipeClientProcessId")
    return pid.value


def pipe_client_session(h: int) -> int:
    sid = c_ulong()
    if not _fn("kernel32", "GetNamedPipeClientSessionId", w.BOOL, w.HANDLE, POINTER(c_ulong))(h, byref(sid)):
        raise _err("GetNamedPipeClientSessionId")
    return sid.value


# ---------- Programm in einer Benutzersitzung starten ----------

class STARTUPINFOW(Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", c_wchar_p), ("lpDesktop", c_wchar_p), ("lpTitle", c_wchar_p),
                ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", c_void_p),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class PROCESS_INFORMATION(Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_NO_WINDOW = 0x08000000


def run_in_session(session_id: int, command_line: str, cwd: str | None = None) -> int:
    """Startet ``command_line`` als angemeldeter Benutzer der Sitzung (Token der Sitzung, Umgebung des Users).
    Nur aus einem Dienst (SYSTEM) möglich. Rückgabe: Prozess-ID."""
    token = w.HANDLE()
    if not _fn("wtsapi32", "WTSQueryUserToken", w.BOOL, w.ULONG, POINTER(w.HANDLE))(session_id, byref(token)):
        raise _err("WTSQueryUserToken")
    env = c_void_p()
    try:
        if not _fn("userenv", "CreateEnvironmentBlock", w.BOOL, POINTER(c_void_p), w.HANDLE, w.BOOL)(
                byref(env), token, False):
            raise _err("CreateEnvironmentBlock")
        try:
            si = STARTUPINFOW(cb=sizeof(STARTUPINFOW), lpDesktop="winsta0\\default")
            pi = PROCESS_INFORMATION()
            cmd = ctypes.create_unicode_buffer(command_line)
            f = _fn("advapi32", "CreateProcessAsUserW", w.BOOL, w.HANDLE, c_wchar_p, c_wchar_p, c_void_p, c_void_p,
                    w.BOOL, w.DWORD, c_void_p, c_wchar_p, POINTER(STARTUPINFOW), POINTER(PROCESS_INFORMATION))
            if not f(token, None, cmd, None, None, False, CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, env, cwd,
                     byref(si), byref(pi)):
                raise _err("CreateProcessAsUser")
            close_handle(pi.hThread)
            close_handle(pi.hProcess)
            return pi.dwProcessId
        finally:
            _fn("userenv", "DestroyEnvironmentBlock", w.BOOL, c_void_p)(env)
    finally:
        close_handle(token)


def kill_in_session(image: str, session_id: int) -> None:
    """Prozesse ``image`` (z. B. LogonUI.exe) in einer Sitzung beenden (taskkill)."""
    import subprocess
    subprocess.run(["taskkill", "/F", "/IM", image, "/FI", f"SESSION eq {session_id}"],
                   capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)


# ---------- Sonstiges ----------

def is_user_admin() -> bool:
    """Läuft der aktuelle Prozess mit Administratorrechten (erhöht)?"""
    return bool(_fn("shell32", "IsUserAnAdmin", w.BOOL)())


def system_ui_language() -> int:
    """LANGID der Systemsprache (primäre Sprache = ``& 0x3FF``, Deutsch = 0x07)."""
    return _fn("kernel32", "GetSystemDefaultUILanguage", w.WORD)()
