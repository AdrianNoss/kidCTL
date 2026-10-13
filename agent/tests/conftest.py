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
    return d
