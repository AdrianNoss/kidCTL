"""Sperre der Issue-Automatik: needs-triage wird nie bearbeitet, feature-request erst mit approved."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev"))
import issues_freigegeben as f  # noqa: E402


def lab(*names):
    return [{"name": n} for n in names]


ISSUES = [
    {"number": 1, "title": "Bug", "labels": []},
    {"number": 2, "title": "Neuer Vorschlag", "labels": lab("feature-request", "needs-triage")},
    {"number": 3, "title": "Freigegeben", "labels": lab("feature-request", "approved")},
    {"number": 4, "title": "Label entfernt, nicht freigegeben", "labels": lab("feature-request")},
    {"number": 5, "title": "approved, aber needs-triage noch dran", "labels": lab("feature-request", "approved",
                                                                                  "needs-triage")},
    {"number": 6, "title": "needs-triage ohne feature-request", "labels": lab("Needs-Triage")},
    {"number": 7, "title": "Bug mit anderem Label", "labels": lab("bug")},
]


def test_filter():
    ok, blocked = f.split(ISSUES)
    assert [i["number"] for i in ok] == [1, 3, 7]
    assert [i["number"] for i in blocked] == [2, 4, 5, 6]


def test_main_outputs_only_released(capsys):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=json.dumps(ISSUES))

    assert f.main(["--repo", "AdrianNoss/kidCTL"], run=fake_run) == 0
    out = capsys.readouterr()
    assert [i["number"] for i in json.loads(out.out)] == [1, 3, 7]
    assert "#2" in out.err and "#5" in out.err
    # nur lesend: ausschließlich „gh issue list“
    assert calls == [["gh", "issue", "list", "--repo", "AdrianNoss/kidCTL", "--state", "open", "--limit", "500",
                      "--json", f.FIELDS]]


def test_task_prompt_uses_script():
    """Der Prompt des Zeitplan-Tasks liegt außerhalb des Repos; wenn vorhanden, muss er das Skript nutzen."""
    task = Path.home() / ".claude/scheduled-tasks/kidctl-issues-taeglich/SKILL.md"
    if not task.exists():
        return
    text = task.read_text(encoding="utf-8")
    assert "dev/issues_freigegeben.py" in text and "needs-triage" in text
