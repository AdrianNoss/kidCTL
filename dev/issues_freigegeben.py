#!/usr/bin/env python3
"""Offene Issues, die die Issue-Automatik (Zeitplan-Task „kidctl-issues-taeglich“) bearbeiten darf.

Sperre für Vorschläge aus der Feature-Inbox (tools/feature-inbox/): Issues mit Label ``needs-triage`` sind immer
gesperrt; Issues mit ``feature-request`` erst, wenn Adrian ``approved`` gesetzt hat. Die Automatik holt ihre Liste
ausschließlich über dieses Skript, nicht direkt über ``gh issue list``.

Aufruf: ``python dev/issues_freigegeben.py [--repo AdrianNoss/kidCTL]`` → JSON-Liste auf stdout (Felder wie
``gh issue list --json number,title,author,labels,updatedAt``); gesperrte Nummern als Hinweis auf stderr.
Nur lesend.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

BLOCK = "needs-triage"
FEATURE = "feature-request"
APPROVED = "approved"
FIELDS = "number,title,author,labels,updatedAt"


def labels(issue: dict) -> set[str]:
    return {(lab.get("name") if isinstance(lab, dict) else str(lab)).strip().lower() for lab in issue.get("labels") or []}


def freigegeben(issue: dict) -> bool:
    names = labels(issue)
    if BLOCK in names:
        return False
    if FEATURE in names and APPROVED not in names:
        return False
    return True


def split(issues: list[dict]) -> tuple[list[dict], list[dict]]:
    ok = [i for i in issues if freigegeben(i)]
    blocked = [i for i in issues if not freigegeben(i)]
    return ok, blocked


def fetch(repo: str, run=subprocess.run) -> list[dict]:
    res = run(["gh", "issue", "list", "--repo", repo, "--state", "open", "--limit", "500", "--json", FIELDS],
              capture_output=True, text=True, check=True)
    return json.loads(res.stdout or "[]")


def main(argv: list[str] | None = None, run=subprocess.run) -> int:
    ap = argparse.ArgumentParser(description="Freigegebene offene Issues für die Issue-Automatik")
    ap.add_argument("--repo", default="AdrianNoss/kidCTL")
    args = ap.parse_args(argv)
    ok, blocked = split(fetch(args.repo, run))
    json.dump(ok, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    if blocked:
        print(f"gesperrt (needs-triage bzw. feature-request ohne approved): "
              f"{', '.join('#' + str(i['number']) for i in blocked)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
