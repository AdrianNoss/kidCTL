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
    monkeypatch.setattr(kidmounts, "STEAM_INSTALLED_DEBIAN", tmp_path / "kein-steam-debian")
    monkeypatch.setattr(kidmounts, "HEROIC_ROOT", tmp_path / "srv-heroic")
    monkeypatch.setattr(kidmounts, "HEROIC_INSTALLED", tmp_path / "kein-heroic")
    # Ebenso nie echte nft-/systemctl-Aufrufe für den DNS-Filter
    from kidctl_agent import dnsfilter
    monkeypatch.setattr(dnsfilter.DnsFilter.__init__, "__defaults__", (fake_run,))
    from kidctl_agent import integrity
    monkeypatch.setattr(integrity, "_run", fake_run)
    monkeypatch.setattr(integrity.check_dns, "__defaults__", (fake_run,))
    # Unabhängig vom Testrechner (CI: Ubuntu, LANG=C): Arch als Distribution, Arch-PAM-Datei, Deutsch als Sprache
    # der Kinder – weder Sitzungsprozesse noch AccountsService oder /etc/locale.conf des Rechners zählen.
    monkeypatch.setattr(integrity, "DPKG_STATUS", tmp_path / "kein-dpkg")
    monkeypatch.setattr(integrity, "PACMAN_DB", tmp_path)
    monkeypatch.setattr(integrity.check_pam, "__defaults__", (tmp_path / "kein-debian-pam",))
    from kidctl_agent import lang
    locale_conf = tmp_path / "locale.conf"
    locale_conf.write_text("LANG=de_DE.UTF-8\n")
    monkeypatch.setattr(lang.system_language, "__defaults__", (locale_conf,))
    monkeypatch.setattr(lang.accounts_language, "__defaults__", (tmp_path / "accounts",))
    monkeypatch.setattr(lang.UserLanguages.__init__, "__defaults__",
                        (lambda uids: [],) + lang.UserLanguages.__init__.__defaults__[1:])
    # Nie den echten Desktop des Entwicklers anfassen (Takt ruft trust_desktop für Programm-Kinder auf)
    monkeypatch.setattr(agent.Agent, "trust_desktops", lambda self: None)
    return d
