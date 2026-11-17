"""Paritäts-Check der Doku: aktuelles Repo ist grün, gezielte Abweichungen werden mit Datei und Zeile gemeldet."""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev"))
import check_docs_parity as cdp  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    for name in ("docs", "website/data"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("img", "releases.json"))
    for f in ("README.md", "README.de.md", "SECURITY.md", "SECURITY.de.md"):
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, tmp_path / f)
    return tmp_path


def edit(path: Path, old: str, new: str) -> int:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return text[: text.index(old)].count("\n") + 1


def test_repo_is_in_parity():
    assert cdp.check(ROOT) == []


def test_heading_only_in_german(repo):
    line = edit(repo / "docs/server.de.md", "## Handy einrichten\n", "## Handy einrichten\n\n### Neu\n")
    errs = cdp.check(repo)
    assert any(e.startswith(f"docs/server.de.md:{line + 2}:") and "Überschrift" in e for e in errs), errs


def test_command_changed_in_english(repo):
    line = edit(repo / "docs/backup.md", "docker compose stop kidctl\n", "docker compose down\n")
    errs = cdp.check(repo)
    assert any(e.startswith("docs/backup.de.md:") and "Codeblock" in e and f"docs/backup.md:{line - 1}" in e
               for e in errs), errs


def test_comment_may_differ(repo):
    edit(repo / "docs/backup.md", "```bash\ndocker compose stop kidctl\n", "```bash\n# stop first\ndocker compose stop kidctl  # really\n")
    assert cdp.check(repo) == []


def test_list_item_only_in_english(repo):
    edit(repo / "docs/installation.md", "- [Updates](updates.md)\n", "- [Updates](updates.md)\n- extra\n")
    errs = cdp.check(repo)
    assert any(e.startswith("docs/installation.md:") and "Listenpunkt" in e for e in errs), errs


def test_link_target_differs(repo):
    edit(repo / "docs/installation.md", "[Updates](updates.md)", "[Updates](backup.md)")
    errs = cdp.check(repo)
    assert any("Link" in e and "updates.md" in e and "backup.md" in e for e in errs), errs


def test_alert_type_differs(repo):
    edit(repo / "docs/server.md", "> [!IMPORTANT]", "> [!NOTE]")
    errs = cdp.check(repo)
    assert any(e.startswith("docs/server.de.md:") and "Hinweisbox" in e for e in errs), errs


def test_missing_translation_and_nav(repo):
    (repo / "docs/new-page.de.md").write_text("# Neu\n", encoding="utf-8")
    errs = cdp.check(repo)
    assert "docs/new-page.de.md:1: Gegenstück docs/new-page.md fehlt" in errs
    assert "docs/new-page.md:1: fehlt in website/data/docs.yaml" in errs


def test_nav_entry_without_file(repo):
    line = edit(repo / "website/data/docs.yaml", "    - backup\n", "    - backup\n    - gibts-nicht\n")
    assert f"website/data/docs.yaml:{line + 1}: Seite 'gibts-nicht' gibt es nicht in docs/" in cdp.check(repo)
