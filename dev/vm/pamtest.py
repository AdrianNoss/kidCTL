#!/usr/bin/env python3
"""PAM-Login testen (nur Test-VM, als root): Authentifizierung + Account-Prüfung.

    sudo python3 pamtest.py <dienst> <user> <passwort>
    z. B.  sudo python3 pamtest.py login kind kind

Ausgabe: Ergebnis von pam_authenticate und pam_acct_mgmt sowie alle PAM-Meldungen
(z. B. Hinweis von pam_echo). Exit-Code 0 = Login erlaubt, 1 = abgelehnt.
"""

import ctypes
import ctypes.util
import sys

libpam = ctypes.CDLL(ctypes.util.find_library("pam"))

PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON, PAM_ERROR_MSG, PAM_TEXT_INFO = 1, 2, 3, 4


class PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


CONV_FUNC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(PamMessage)),
                             ctypes.POINTER(ctypes.POINTER(PamResponse)), ctypes.c_void_p)


class PamConv(ctypes.Structure):
    _fields_ = [("conv", CONV_FUNC), ("appdata_ptr", ctypes.c_void_p)]


libc = ctypes.CDLL(ctypes.util.find_library("c"))
libc.calloc.restype = ctypes.c_void_p
libc.strdup.restype = ctypes.c_void_p
libpam.pam_strerror.restype = ctypes.c_char_p


def main() -> int:
    service, user, password = sys.argv[1:4]
    messages: list[str] = []

    @CONV_FUNC
    def conv(n, msgs, resp, _data):
        arr = libc.calloc(n, ctypes.sizeof(PamResponse))
        resp[0] = ctypes.cast(arr, ctypes.POINTER(PamResponse))
        for i in range(n):
            m = msgs[i].contents
            if m.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                resp[0][i].resp = libc.strdup(password.encode())
            else:
                kind = "INFO" if m.msg_style == PAM_TEXT_INFO else "FEHLER"
                messages.append(f"{kind}: {m.msg.decode(errors='replace')}")
        return 0

    handle = ctypes.c_void_p()
    rc = libpam.pam_start(service.encode(), user.encode(), ctypes.byref(PamConv(conv, None)),
                          ctypes.byref(handle))
    if rc:
        print("pam_start fehlgeschlagen", rc)
        return 2
    auth = libpam.pam_authenticate(handle, 0)
    acct = libpam.pam_acct_mgmt(handle, 0) if auth == 0 else None
    print(f"[{service}/{user}] authenticate: {libpam.pam_strerror(handle, auth).decode()}")
    if acct is not None:
        print(f"[{service}/{user}] acct_mgmt:    {libpam.pam_strerror(handle, acct).decode()}")
    for m in messages:
        print("  " + m)
    libpam.pam_end(handle, acct or auth)
    ok = auth == 0 and acct == 0
    print(f"=> {'ERLAUBT' if ok else 'ABGELEHNT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
