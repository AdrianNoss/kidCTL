"""Persistenter Zustand unter /var/lib/kidctl (root:root, Dateien 0600, atomar geschrieben)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

STATE_DIR = Path(os.environ.get("KIDCTL_STATE_DIR", "/var/lib/kidctl"))

DEVICE_FILE = "device.json"   # server_url, device_id, token
POLICY_FILE = "policy.json"   # letzte Policy vom Server (Offline-Durchsetzung)
USAGE_FILE = "usage.json"     # lokale Zählerstände + Nutzung anderer Geräte


def path(name: str) -> Path:
    return STATE_DIR / name


def ensure_dir() -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)


def load(name: str) -> dict | None:
    try:
        return json.loads(path(name).read_text())
    except FileNotFoundError:
        return None


def save(name: str, data: dict) -> None:
    """Atomar: temporäre Datei (0600) schreiben, fsync, umbenennen."""
    ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path(name))
    except BaseException:
        os.unlink(tmp)
        raise
