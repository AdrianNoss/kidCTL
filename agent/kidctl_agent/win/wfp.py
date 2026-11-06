"""Sperren je Benutzer über die Windows Filtering Platform (WFP, ``fwpuclnt.dll``).

Windows-Firewall-Regeln mit „Lokaler Benutzer“ wirken ausgehend nicht (nur mit IPsec) – deshalb eigene Filter
auf ``ALE_AUTH_CONNECT`` (V4/V6) mit der Bedingung ``ALE_USER_ID`` (Sicherheitsbeschreibung mit der SID des
Kindes) und optional dem Zielport. Die Sitzung ist **dynamisch**: Endet der Agent (auch durch Absturz), entfernt
Windows alle Filter von selbst – es bleiben nie verwaiste Sperren zurück; der Dienst startet neu und setzt sie
wieder.

Verwendet für: DNS (53) und DNS-over-TLS/QUIC (853) für Kinder mit Website-Filter (eigene Resolver in Apps,
``nslookup x 8.8.8.8``), und die Netzsperre für Kinder-Sitzungen im Hintergrund (alle neuen Verbindungen).
"""

from __future__ import annotations

import ctypes
import logging
import uuid
from ctypes import POINTER, Structure, Union, byref, c_void_p, c_wchar_p
from ctypes import wintypes as w

from . import api

log = logging.getLogger("kidctl.wfp")

RPC_C_AUTHN_WINNT = 10
FWPM_SESSION_FLAG_DYNAMIC = 0x1
FWP_EMPTY, FWP_UINT8, FWP_UINT16, FWP_UINT32, FWP_UINT64 = 0, 1, 2, 3, 4
FWP_SECURITY_DESCRIPTOR_TYPE = 14
FWP_MATCH_EQUAL = 0
FWP_ACTION_BLOCK = 0x1001
FWP_ACTRL_MATCH_FILTER = 0x1

LAYER_CONNECT_V4 = uuid.UUID("c38d57d1-05a7-4c33-904f-7fbceee60e82")
LAYER_CONNECT_V6 = uuid.UUID("4a72393b-319f-44bc-84c3-ba54dcb3b6b4")
COND_REMOTE_PORT = uuid.UUID("c35a604d-d22b-4e1a-91b4-68f674ee674b")
COND_USER_ID = uuid.UUID("af043a0a-b34d-4f86-979c-c90371af6e66")


class GUID(Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, u: uuid.UUID) -> "GUID":
        g = cls()
        ctypes.memmove(byref(g), u.bytes_le, 16)
        return g


class DISPLAY_DATA(Structure):
    _fields_ = [("name", c_wchar_p), ("description", c_wchar_p)]


class BYTE_BLOB(Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", c_void_p)]


class _VALUE_UNION(Union):
    _fields_ = [("uint8", ctypes.c_uint8), ("uint16", ctypes.c_uint16), ("uint32", ctypes.c_uint32),
                ("uint64", POINTER(ctypes.c_uint64)), ("sd", POINTER(BYTE_BLOB)), ("ptr", c_void_p)]


class VALUE(Structure):   # FWP_VALUE0 und FWP_CONDITION_VALUE0 (gleiches Layout)
    _fields_ = [("type", ctypes.c_int), ("u", _VALUE_UNION)]


class FILTER_CONDITION(Structure):
    _fields_ = [("fieldKey", GUID), ("matchType", ctypes.c_int), ("conditionValue", VALUE)]


class _ACTION_UNION(Union):
    _fields_ = [("filterType", GUID), ("calloutKey", GUID)]


class ACTION(Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _ACTION_UNION)]


class _CONTEXT_UNION(Union):
    _fields_ = [("rawContext", ctypes.c_uint64), ("providerContextKey", GUID)]


class FILTER(Structure):
    _fields_ = [("filterKey", GUID), ("displayData", DISPLAY_DATA), ("flags", ctypes.c_uint32),
                ("providerKey", POINTER(GUID)), ("providerData", BYTE_BLOB), ("layerKey", GUID),
                ("subLayerKey", GUID), ("weight", VALUE), ("numFilterConditions", ctypes.c_uint32),
                ("filterCondition", POINTER(FILTER_CONDITION)), ("action", ACTION), ("ctx", _CONTEXT_UNION),
                ("reserved", POINTER(GUID)), ("filterId", ctypes.c_uint64), ("effectiveWeight", VALUE)]


class SUBLAYER(Structure):
    _fields_ = [("subLayerKey", GUID), ("displayData", DISPLAY_DATA), ("flags", ctypes.c_uint32),
                ("providerKey", POINTER(GUID)), ("providerData", BYTE_BLOB), ("weight", ctypes.c_uint16)]


class SESSION(Structure):
    _fields_ = [("sessionKey", GUID), ("displayData", DISPLAY_DATA), ("flags", ctypes.c_uint32),
                ("txnWaitTimeoutInMSec", ctypes.c_uint32), ("processId", w.DWORD), ("sid", c_void_p),
                ("username", c_wchar_p), ("kernelMode", w.BOOL)]


def _fn(name, *argtypes):
    return api._fn("fwpuclnt", name, w.DWORD, *argtypes)


def _check(what: str, rc: int) -> None:
    if rc != 0:
        raise OSError(rc, f"{what}: 0x{rc:08x}")


def _user_sd(sid: str) -> bytes:
    """Selbstrelative Sicherheitsbeschreibung, die ``sid`` FWP_ACTRL_MATCH_FILTER gewährt."""
    conv = api._fn("advapi32", "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL, c_wchar_p,
                   w.DWORD, POINTER(c_void_p), POINTER(w.ULONG))
    psd, size = c_void_p(), w.ULONG()
    if not conv(f"O:LSD:(A;;CC;;;{sid})", 1, byref(psd), byref(size)):
        raise api._err("ConvertStringSecurityDescriptorToSecurityDescriptor")
    try:
        return ctypes.string_at(psd, size.value)
    finally:
        api._local_free(psd)


class Wfp:
    """Dynamische WFP-Sitzung mit eigener Unterschicht. ``set_rules`` gleicht die Filter ab."""

    def __init__(self) -> None:
        self.engine = w.HANDLE()
        self.sublayer = uuid.uuid4()
        self.filters: dict[tuple, list[int]] = {}   # (SID, Port|None) -> Filter-IDs
        self._keep: list = []   # Puffer, die WFP referenziert, bis der Filter angelegt ist

    def open(self) -> None:
        if self.engine:
            return
        session = SESSION(displayData=DISPLAY_DATA("KidCTL", "Kinderschutz: DNS- und Netzsperren"),
                          flags=FWPM_SESSION_FLAG_DYNAMIC)
        _check("FwpmEngineOpen0", _fn("FwpmEngineOpen0", c_wchar_p, ctypes.c_uint32, c_void_p, POINTER(SESSION),
                                      POINTER(w.HANDLE))(None, RPC_C_AUTHN_WINNT, None, byref(session),
                                                        byref(self.engine)))
        sub = SUBLAYER(subLayerKey=GUID.of(self.sublayer), displayData=DISPLAY_DATA("KidCTL", "KidCTL"),
                       weight=0xFFFF)
        _check("FwpmSubLayerAdd0", _fn("FwpmSubLayerAdd0", w.HANDLE, POINTER(SUBLAYER), c_void_p)(
            self.engine, byref(sub), None))
        log.info("WFP-Sitzung geöffnet (dynamisch)")

    def close(self) -> None:
        if self.engine:
            _fn("FwpmEngineClose0", w.HANDLE)(self.engine)
            self.engine = w.HANDLE()
            self.filters.clear()

    def _add(self, layer: uuid.UUID, sid: str, port: int | None, name: str) -> int:
        sd = _user_sd(sid)
        sd_buf = ctypes.create_string_buffer(sd, len(sd))
        blob = BYTE_BLOB(len(sd), ctypes.cast(sd_buf, c_void_p))
        conds = [FILTER_CONDITION(GUID.of(COND_USER_ID), FWP_MATCH_EQUAL,
                                  VALUE(FWP_SECURITY_DESCRIPTOR_TYPE, _VALUE_UNION(sd=ctypes.pointer(blob))))]
        if port is not None:
            conds.append(FILTER_CONDITION(GUID.of(COND_REMOTE_PORT), FWP_MATCH_EQUAL,
                                          VALUE(FWP_UINT16, _VALUE_UNION(uint16=port))))
        arr = (FILTER_CONDITION * len(conds))(*conds)
        f = FILTER(filterKey=GUID.of(uuid.uuid4()), displayData=DISPLAY_DATA(name, name),
                   layerKey=GUID.of(layer), subLayerKey=GUID.of(self.sublayer), weight=VALUE(FWP_EMPTY),
                   numFilterConditions=len(conds), filterCondition=arr, action=ACTION(FWP_ACTION_BLOCK))
        fid = ctypes.c_uint64()
        _check("FwpmFilterAdd0", _fn("FwpmFilterAdd0", w.HANDLE, POINTER(FILTER), c_void_p, POINTER(ctypes.c_uint64))(
            self.engine, byref(f), None, byref(fid)))
        return fid.value

    def _delete(self, fid: int) -> None:
        _fn("FwpmFilterDeleteById0", w.HANDLE, ctypes.c_uint64)(self.engine, fid)

    def set_rules(self, wanted: set[tuple[str, int | None]]) -> None:
        """``wanted``: (SID, Zielport) – Port ``None`` sperrt alle neuen Verbindungen des Benutzers."""
        self.open()
        for key in list(self.filters):
            if key not in wanted:
                for fid in self.filters.pop(key):
                    self._delete(fid)
        for sid, port in sorted(wanted - set(self.filters), key=str):
            name = f"KidCTL {sid} " + (f"Port {port}" if port is not None else "alles")
            ids = []
            try:
                for layer in (LAYER_CONNECT_V4, LAYER_CONNECT_V6):
                    ids.append(self._add(layer, sid, port, name))
            except OSError:
                for fid in ids:
                    self._delete(fid)
                raise
            self.filters[(sid, port)] = ids
