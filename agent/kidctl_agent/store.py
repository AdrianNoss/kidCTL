"""Persistenter Zustand unter /var/lib/kidctl (root:root, Dateien 0600, atomar geschrieben).

Windows: ``%ProgramData%\\KidCTL\\state`` – die Rechte (nur SYSTEM und Administratoren) setzt der Installer
auf ``%ProgramData%\\KidCTL``; neue Dateien erben sie."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

WINDOWS = sys.platform == "win32"
PROGRAM_DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "KidCTL"
STATE_DIR = Path(os.environ.get("KIDCTL_STATE_DIR") or (PROGRAM_DATA / "state" if WINDOWS else "/var/lib/kidctl"))

DEVICE_FILE = "device.json"   # server_url, device_id, token
POLICY_FILE = "policy.json"   # letzte Policy vom Server (Offline-Durchsetzung)
USAGE_FILE = "usage.json"     # lokale Zählerstände + Nutzung anderer Geräte


def path(name: str) -> Path:
    return STATE_DIR / name


def ensure_dir() -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not WINDOWS:
        os.chmod(STATE_DIR, 0o700)


def load(name: str) -> dict | None:
    try:
        return json.loads(path(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def save(name: str, data: dict) -> None:
    """Atomar: temporäre Datei (0600) schreiben, fsync, umbenennen."""
    ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if not WINDOWS:
                os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path(name))
    except BaseException:
        os.unlink(tmp)
        raise
