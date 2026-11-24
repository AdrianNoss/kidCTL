"""Ersteinrichtung: Schlüssel-Befehle der CLI laufen, bevor .env vollständig ist (ohne SECRET_KEY/KIDCTL_MASTER_KEY)."""

import base64
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
ENV = {"PATH": os.environ["PATH"], "PYTHONPATH": f"{SERVER}:{SERVER.parent / 'common'}"}


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "app.cli", *args], cwd=SERVER, env=ENV, capture_output=True, text=True)


def test_master_key_without_config():
    r = _cli("master-key")
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert line.startswith("KIDCTL_MASTER_KEY=")
    key = line.split("=", 1)[1]
    assert len(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))) == 32
    assert "Getrennt sichern" in r.stderr
    assert _cli("master-key").stdout != r.stdout


def test_vapid_keys_without_config():
    r = _cli("vapid-keys")
    assert r.returncode == 0, r.stderr
    assert "VAPID_PRIVATE_KEY=" in r.stdout and "VAPID_PUBLIC_KEY=" in r.stdout


def test_other_commands_still_need_config():
    r = _cli("backup")
    assert r.returncode != 0 and "fehlt" in r.stderr
