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
    # Nie echte Mounts/systemd-Aufrufe auf dem Entwicklungsrechner (CLAUDE.md)
    from kidctl_agent import kidmounts
    calls = []

    def fake_run(*cmd, **kw):
        import subprocess
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(kidmounts, "_run", fake_run)
    monkeypatch.setattr(kidmounts.KidMounts.__init__, "__defaults__", (fake_run, lambda: set(), lambda h: False))
    monkeypatch.setattr(kidmounts, "STEAM_ROOT", tmp_path / "srv-steam")
    monkeypatch.setattr(kidmounts, "STEAM_INSTALLED", tmp_path / "kein-steam")
    monkeypatch.setattr(kidmounts, "HEROIC_ROOT", tmp_path / "srv-heroic")
    monkeypatch.setattr(kidmounts, "HEROIC_INSTALLED", tmp_path / "kein-heroic")
    # Ebenso nie echte nft-/systemctl-Aufrufe für den DNS-Filter
    from kidctl_agent import dnsfilter
    monkeypatch.setattr(dnsfilter.DnsFilter.__init__, "__defaults__", (fake_run,))
    from kidctl_agent import integrity
    monkeypatch.setattr(integrity, "_run", fake_run)
    monkeypatch.setattr(integrity.check_dns, "__defaults__", (fake_run,))
    # Nie den echten Desktop des Entwicklers anfassen (Takt ruft trust_desktop für Programm-Kinder auf)
    monkeypatch.setattr(agent.Agent, "trust_desktops", lambda self: None)
    return d
