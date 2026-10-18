"""Interpreter: Code-Einschleusung über Umgebung und Python-User-Site."""

from kidctl_agent.programs import Allowed, classify, interpreter_injection
from test_programs import proc

SCRIPT = "/usr/lib/kidctl/tray.py"
PY = proc("/usr/bin/python3.14", ["python3", SCRIPT])


def allowed(home=""):
    return Allowed(["/usr/lib/kidctl/*"], set(), frozenset(), [], home=home)


def test_clean_script_allowed(tmp_path):
    assert classify(PY, allowed(str(tmp_path)), environ=lambda: {"PATH": "/usr/bin"}) is None


def test_env_injection_blocked(tmp_path):
    for env in ({"PYTHONPATH": "/home/kind/lib"}, {"PYTHONPATH": "/usr/lib/foo:"},   # leer = cwd
                {"PYTHONUSERBASE": "/home/kind/x"}, {"PYTHONSTARTUP": "/usr/x.py"}, {"PYTHONINSPECT": "1"},
                {"PYTHONPATH": "lib"}):
        v = classify(PY, allowed(str(tmp_path)), environ=lambda e=env: e)
        assert v is not None and "eingeschleuster Code" in v.detail, env
    # Systempfade sind in Ordnung
    assert classify(PY, allowed(), environ=lambda: {"PYTHONPATH": "/usr/lib/foo"}) is None
    assert interpreter_injection("perl", {"PERL5OPT": "-Mevil"}, "") == "PERL5OPT"
    assert interpreter_injection("lua", {"LUA_PATH": "/usr/share/lua/5.4/?.lua;;"}, "") is None
    assert interpreter_injection("lua", {"LUA_PATH": "/home/kind/?.lua;;"}, "") == "LUA_PATH"


def test_python_user_site_blocked(tmp_path):
    site = tmp_path / ".local/lib/python3.14/site-packages"
    site.mkdir(parents=True)
    assert classify(PY, allowed(str(tmp_path)), environ=lambda: {}) is None      # leer: ok
    (site / "x.pth").write_text("import os\n")
    v = classify(PY, allowed(str(tmp_path)), environ=lambda: {})
    assert v.key == f"exe:{SCRIPT}" and "site-packages" in v.detail
    # gilt nur für Python
    assert interpreter_injection("perl", {}, str(tmp_path)) is None
