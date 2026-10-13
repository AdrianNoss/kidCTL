import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "common")]


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Jeder Test mit eigenem, leerem Zustandsverzeichnis statt /var/lib/kidctl."""
    from kidctl_agent import store
    d = tmp_path / "state"
    monkeypatch.setattr(store, "STATE_DIR", d)
    monkeypatch.setenv("KIDCTL_STATE_DIR", str(d))
    from kidctl_agent import loginlock
    monkeypatch.setattr(loginlock, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(loginlock, "is_admin", lambda u: False)  # Testsystem-User unabhängig
    from kidctl_agent import agent
    monkeypatch.setattr(agent, "is_admin", lambda u: False)
    return d
