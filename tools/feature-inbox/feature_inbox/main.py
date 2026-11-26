"""Ablauf: Postfach lesen → v1 parsen → aufbereiten → Issue anlegen → Mail verschieben; täglich Übersicht.

Aufruf: ``python -m feature_inbox [--apply] [--loop]``. Ohne ``--apply`` Probelauf: liest nur, legt keine Issues
an, verschiebt nichts und schickt keine Mail.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field

from . import issue, llm, parse, report
from .config import Config
from .github import GitHub, GitHubError
from .imapbox import Mailbox
from .state import State

log = logging.getLogger("feature_inbox")


@dataclass
class Stats:
    created: list[int] = field(default_factory=list)
    skipped: int = 0
    unsorted: int = 0
    errors: int = 0


def process_once(cfg: Config, box, gh, model, state: State, apply: bool = True) -> Stats:
    stats = Stats()
    mails = box.unseen()
    if not mails:
        return stats
    all_issues: list[dict] | None = None

    for uid, raw in mails:
        req = parse.parse(raw)
        if req is None:
            stats.unsorted += 1
            log.info("Mail ohne v1-Format → %s", cfg.imap_unsorted)
            if apply:
                box.move(uid, cfg.imap_unsorted)
                state.event("unsorted")
            continue
        try:
            if state.known(req.request_id) is not None:
                stats.skipped += 1
                if apply:
                    box.move(uid, cfg.imap_done)
                continue
            if all_issues is None:
                all_issues = gh.feature_issues(state="all")
            existing = next((i for i in all_issues if issue.request_id_of(i.get("body")) == req.request_id), None)
            if existing is not None:  # z. B. Abbruch nach dem Anlegen im letzten Lauf
                stats.skipped += 1
                if apply:
                    state.record(req.request_id, existing["number"], req.contact, [])
                    box.move(uid, cfg.imap_done)
                continue
            open_issues = [i for i in all_issues if i.get("state") == "open"]
            cands = issue.candidates(req, open_issues)
            analysis = model.analyze(req, [(n, t) for n, t, _ in cands])
            dups = issue.duplicates(analysis, cands)
            title, body = issue.title(req), issue.body(req, analysis, dups)
            if not apply:
                log.info("Probelauf: würde Issue anlegen (Request %s, Kontakt %s, Duplikate %s)",
                         req.request_id, parse.mask_email(req.contact), dups)
                continue
            number = gh.create_issue(title, body)
            state.record(req.request_id, number, req.contact, dups)
            all_issues.append({"number": number, "title": title, "body": body, "state": "open"})
            box.move(uid, cfg.imap_done)
            state.event("new", str(number))
            if dups:
                state.event("duplicate", f"{number} (≈ {', '.join(f'#{d}' for d in dups)})")
            stats.created.append(number)
            log.info("Issue #%d angelegt (Request %s, Kontakt %s)", number, req.request_id,
                     parse.mask_email(req.contact))
        except (GitHubError, OSError, RuntimeError) as exc:
            # Mail bleibt ungelesen (BODY.PEEK) und wird im nächsten Lauf erneut versucht
            stats.errors += 1
            log.error("Request %s nicht verarbeitet: %s", req.request_id, exc)
            if apply:
                state.event("error", req.request_id)
    return stats


def maybe_report(cfg: Config, state: State, now: float | None = None, send=report.send) -> bool:
    """Schickt die Übersicht einmal pro Tag ab ``FEATURE_INBOX_REPORT_HOUR`` (Ortszeit, ``TZ``)."""
    if not cfg.report_to:
        return False
    now = time.time() if now is None else now
    lt = time.localtime(now)
    today = time.strftime("%Y-%m-%d", lt)
    if lt.tm_hour < cfg.report_hour or state.get_meta("last_report_day") == today:
        return False
    since = float(state.get_meta("last_report_at") or now - 86400)
    subject, body = report.text(state.events_since(since), cfg.github_repo)
    send(cfg, subject, body)
    state.set_meta("last_report_day", today)
    state.set_meta("last_report_at", str(now))
    return True


def run(cfg: Config, apply: bool) -> Stats:
    state = State(cfg.state_path)
    gh = GitHub(cfg.github_repo, cfg.github_token)
    model = llm.make(cfg.llm, cfg.model)
    box = Mailbox(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password, cfg.imap_inbox)
    try:
        stats = process_once(cfg, box, gh, model, state, apply=apply)
    finally:
        box.close()
    if apply:
        try:
            maybe_report(cfg, state)
        except Exception as exc:  # Übersicht darf den Abruf nicht stoppen
            log.error("Tagesübersicht nicht gesendet: %s", type(exc).__name__)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="feature_inbox", description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="wirklich Issues anlegen, Mails verschieben, Übersicht senden")
    ap.add_argument("--loop", action="store_true", help="dauerhaft im Intervall FEATURE_INBOX_INTERVAL_MIN laufen")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if missing := cfg.missing():
        log.error("Fehlende Einstellungen: %s", ", ".join(missing))
        return 2
    if not args.apply:
        log.info("Probelauf (ohne --apply): nichts wird angelegt, verschoben oder gesendet")
    while True:
        try:
            s = run(cfg, args.apply)
            log.info("Lauf fertig: %d neu, %d schon bekannt, %d unsortiert, %d Fehler",
                     len(s.created), s.skipped, s.unsorted, s.errors)
        except Exception as exc:  # IMAP nicht erreichbar o. ä.: nächster Versuch im nächsten Intervall
            log.error("Lauf abgebrochen: %s", type(exc).__name__)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(cfg.interval_s)


if __name__ == "__main__":
    sys.exit(main())
