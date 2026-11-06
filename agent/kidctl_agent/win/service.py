"""Windows-Dienst ``KidCTL`` ohne pywin32: ``python.exe -m kidctl_agent service`` meldet sich per ctypes beim
Dienststeuerungs-Manager (SCM) an und lässt den Agenten in ``ServiceMain`` laufen.

Den Dienst legt der Installer an (``LocalSystem``, automatischer Start, Neustart bei Absturz, Eintrag unter
``SafeBoot\\Minimal`` und ``\\Network``). Stoppen dürfen nur Administratoren.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import logging.handlers
from ctypes import POINTER, Structure, byref, c_void_p, c_wchar_p
from ctypes import wintypes as w

from .. import store
from . import api

log = logging.getLogger("kidctl.service")

SERVICE_NAME = "KidCTL"
STOP_REASON: dict[str, int] = {}   # "control": Steuercode, mit dem der Dienst beendet wurde (Stopp/Herunterfahren)
LOG_DIR = store.PROGRAM_DATA / "logs"


def setup_logging(level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "agent.log", maxBytes=5 * 1024 * 1024, backupCount=5,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def run_service(make_main) -> None:
    """``make_main()`` liefert die Koroutine des Agenten. Blockiert, bis der Dienst gestoppt ist."""
    SERVICE_MAIN = ctypes.WINFUNCTYPE(None, w.DWORD, POINTER(c_wchar_p))
    HANDLER_EX = ctypes.WINFUNCTYPE(w.DWORD, w.DWORD, w.DWORD, c_void_p, c_void_p)

    class SERVICE_TABLE_ENTRYW(Structure):
        _fields_ = [("lpServiceName", c_wchar_p), ("lpServiceProc", SERVICE_MAIN)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    register = advapi.RegisterServiceCtrlHandlerExW
    register.restype, register.argtypes = c_void_p, [c_wchar_p, HANDLER_EX, c_void_p]
    set_status = advapi.SetServiceStatus
    set_status.restype, set_status.argtypes = w.BOOL, [c_void_p, POINTER(api.SERVICE_STATUS)]

    state: dict = {"handle": None, "loop": None, "task": None}
    status = api.SERVICE_STATUS(api.SERVICE_WIN32_OWN_PROCESS, api.SERVICE_START_PENDING, 0, 0, 0, 0, 3000)

    def report(current: int, wait_hint: int = 0) -> None:
        status.dwCurrentState = current
        status.dwControlsAccepted = (api.SERVICE_ACCEPT_STOP | api.SERVICE_ACCEPT_SHUTDOWN
                                     if current == api.SERVICE_RUNNING else 0)
        status.dwWaitHint = wait_hint
        status.dwCheckPoint = 0 if current in (api.SERVICE_RUNNING, api.SERVICE_STOPPED) else status.dwCheckPoint + 1
        if state["handle"]:
            set_status(state["handle"], byref(status))

    def handler(control, _event_type, _event_data, _context):
        if control in (api.SERVICE_CONTROL_STOP, api.SERVICE_CONTROL_SHUTDOWN):
            log.info("Dienst wird beendet (Steuercode %s)", control)
            STOP_REASON["control"] = control
            report(api.SERVICE_STOP_PENDING, 15000)
            loop, task = state["loop"], state["task"]
            if loop and task:
                loop.call_soon_threadsafe(task.cancel)
        elif control == api.SERVICE_CONTROL_INTERROGATE:
            report(status.dwCurrentState)
        return api.NO_ERROR

    handler_cb = HANDLER_EX(handler)

    def service_main(_argc, _argv):
        state["handle"] = register(SERVICE_NAME, handler_cb, None)
        if not state["handle"]:
            log.error("RegisterServiceCtrlHandlerEx fehlgeschlagen (%s)", ctypes.get_last_error())
            return
        report(api.SERVICE_START_PENDING, 10000)

        async def main() -> None:
            state["loop"] = asyncio.get_running_loop()
            state["task"] = asyncio.current_task()
            report(api.SERVICE_RUNNING)
            await make_main()

        try:
            asyncio.run(main())
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Agent mit Fehler beendet")
            status.dwWin32ExitCode = 1064   # ERROR_EXCEPTION_IN_SERVICE -> SCM startet neu (Wiederherstellung)
        log.info("Agent beendet")
        report(api.SERVICE_STOPPED)

    main_cb = SERVICE_MAIN(service_main)
    table = (SERVICE_TABLE_ENTRYW * 2)(SERVICE_TABLE_ENTRYW(SERVICE_NAME, main_cb), SERVICE_TABLE_ENTRYW(None, SERVICE_MAIN()))
    dispatcher = advapi.StartServiceCtrlDispatcherW
    dispatcher.restype, dispatcher.argtypes = w.BOOL, [POINTER(SERVICE_TABLE_ENTRYW)]
    if not dispatcher(table):
        raise OSError(ctypes.get_last_error(), "StartServiceCtrlDispatcher fehlgeschlagen – nur als Dienst starten")
