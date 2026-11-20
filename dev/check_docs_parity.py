#!/usr/bin/env python3
"""Paritäts-Check der zweisprachigen Doku: Deutsch (Quelle) und Englisch müssen den gleichen Inhalt haben.

Paare: docs/<name>.de.md ↔ docs/<name>.md, README.de.md ↔ README.md, SECURITY.de.md ↔ SECURITY.md,
CONTRIBUTING.de.md ↔ CONTRIBUTING.md.
Geprüft wird je Paar:
  - beide Dateien vorhanden
  - gleiche Überschriftenfolge (Anzahl und Ebenen)
  - Codeblöcke identisch (Sprache und Inhalt; Kommentare `#`, `//`, `REM` dürfen abweichen)
  - gleiche Anzahl Tabellenzeilen, Listenpunkte und Hinweisboxen je Typ (`> [!NOTE]` …)
  - gleiche Linkziele (Anker und `.de.md` ↔ `.md` dürfen abweichen) und gleiche Bilder (`img/de/` ↔ `img/en/`)
Außerdem: jede docs/-Seite steht in website/data/docs.yaml und jeder Eintrag dort existiert.

    python dev/check_docs_parity.py [REPO]     Ausgabe: <datei>:<zeile>: <Fehler>; Exit 1 bei Abweichung
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALERT = re.compile(r"^\s*>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]")
HEADING = re.compile(r"^(#{1,6})\s+\S")
LIST = re.compile(r"^\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\S")
TABLE = re.compile(r"^\s*(?:>\s*)*\|.*\|\s*$")
TABLE_SEP = re.compile(r"^\s*(?:>\s*)*\|[\s:|-]+\|\s*$")
LINK = re.compile(r"(!?)\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^(\s*(?:>\s*)*)(```+|~~~+)\s*([\w+-]*)")
COMMENT_LINE = re.compile(r"^\s*(#|//|REM\b|rem\b|::)")
TRAILING_COMMENT = re.compile(r"\s+(#|//)\s.*$")


@dataclass
class Doc:
    path: Path
    headings: list[tuple[int, int]] = field(default_factory=list)        # (Zeile, Ebene)
    code: list[tuple[int, str, str]] = field(default_factory=list)       # (Zeile, Sprache, normierter Inhalt)
    lists: list[int] = field(default_factory=list)
    tables: list[int] = field(default_factory=list)
    alerts: list[tuple[int, str]] = field(default_factory=list)
    links: list[tuple[int, str]] = field(default_factory=list)
    images: list[tuple[int, str]] = field(default_factory=list)


def norm_code(lines: list[str]) -> str:
    out = []
    for line in lines:
        if COMMENT_LINE.match(line):
            continue
        out.append(TRAILING_COMMENT.sub("", line).rstrip())
    return "\n".join(out).strip()


def norm_target(t: str) -> str:
    t = t.split("#", 1)[0]
    t = re.sub(r"(adriannoss\.github\.io/kidCTL)/de/", r"\1/", t)   # Wiki-URL der jeweiligen Sprache
    t = re.sub(r"\.de\.md$", ".md", t)
    t = re.sub(r"(^|/)img/(de|en)/", r"\1img/<lang>/", t)
    return t or "#"


def parse(path: Path) -> Doc:
    d = Doc(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        n = i + 1
        m = FENCE.match(line)
        if m:
            prefix, fence, lang = m.groups()
            body = []
            i += 1
            while i < len(lines) and not lines[i].lstrip(" >").startswith(fence):
                body.append(lines[i][len(prefix):] if lines[i].startswith(prefix) else lines[i].lstrip(" >"))
                i += 1
            d.code.append((n, lang, norm_code(body)))
            i += 1
            continue
        if h := HEADING.match(line):
            d.headings.append((n, len(h.group(1))))
        if a := ALERT.match(line):
            d.alerts.append((n, a.group(1)))
        if LIST.match(line):
            d.lists.append(n)
        if TABLE.match(line) and not TABLE_SEP.match(line):
            d.tables.append(n)
        for bang, target in LINK.findall(re.sub(r"`[^`]*`", "", line)):
            (d.images if bang else d.links).append((n, norm_target(target)))
        i += 1
    return d


def compare(de: Doc, en: Doc, root: Path) -> list[str]:
    errs: list[str] = []
    rd, re_ = de.path.relative_to(root), en.path.relative_to(root)

    def at(doc_rel, line, msg):
        errs.append(f"{doc_rel}:{line}: {msg}")

    def seq(name, a, b, key=lambda x: x[1]):
        for k, (x, y) in enumerate(zip(a, b)):
            if key(x) != key(y):
                at(rd, x[0], f"{name} Nr. {k + 1} weicht ab: {key(x)!r} ≠ {key(y)!r} ({re_}:{y[0]})")
                return
        if len(a) != len(b):
            k = min(len(a), len(b))
            more, other, line = (rd, re_, a[k][0]) if len(a) > len(b) else (re_, rd, b[k][0])
            at(more, line, f"{name}: {len(a)} (de) ≠ {len(b)} (en) – Nr. {k + 1} fehlt in {other}")

    seq("Überschrift", de.headings, en.headings, key=lambda x: "#" * x[1])
    seq("Codeblock", de.code, en.code, key=lambda x: (x[1], x[2]))
    seq("Tabellenzeile", [(n, 1) for n in de.tables], [(n, 1) for n in en.tables])
    seq("Listenpunkt", [(n, 1) for n in de.lists], [(n, 1) for n in en.lists])
    seq("Hinweisbox", de.alerts, en.alerts)
    seq("Link", de.links, en.links)
    seq("Bild", de.images, en.images)
    return errs


def pairs(root: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    out, errs = [], []
    docs = root / "docs"
    names = sorted({p.name.removesuffix(".de.md").removesuffix(".md") for p in docs.glob("*.md")})
    for name in names:
        de, en = docs / f"{name}.de.md", docs / f"{name}.md"
        for have, miss in ((de, en), (en, de)):
            if have.exists() and not miss.exists():
                errs.append(f"{have.relative_to(root)}:1: Gegenstück {miss.relative_to(root)} fehlt")
        if de.exists() and en.exists():
            out.append((de, en))
    for base in ("README", "SECURITY", "CONTRIBUTING"):
        de, en = root / f"{base}.de.md", root / f"{base}.md"
        if de.exists() or en.exists():
            if de.exists() and en.exists():
                out.append((de, en))
            else:
                have, miss = (de, en) if de.exists() else (en, de)
                errs.append(f"{have.relative_to(root)}:1: Gegenstück {miss.name} fehlt")
    return out, errs


def check_nav(root: Path) -> list[str]:
    """docs.yaml (Navigation des Wikis) gegen die Dateien in docs/."""
    nav = root / "website/data/docs.yaml"
    if not nav.exists():
        return ["website/data/docs.yaml:1: fehlt"]
    entries: list[tuple[int, str]] = []
    for n, line in enumerate(nav.read_text(encoding="utf-8").splitlines(), 1):
        if m := re.match(r"^\s*-\s+([a-z0-9-]+)\s*(#.*)?$", line):
            entries.append((n, m.group(1)))
    errs = []
    pages = {p.name.removesuffix(".de.md").removesuffix(".md") for p in (root / "docs").glob("*.md")}
    if (root / "SECURITY.md").exists():
        pages.add("security")   # Einzeldatei-Mount von SECURITY.md/SECURITY.de.md
    listed = [e for _, e in entries]
    for n, e in entries:
        if e not in pages:
            errs.append(f"website/data/docs.yaml:{n}: Seite {e!r} gibt es nicht in docs/")
        if listed.count(e) > 1:
            errs.append(f"website/data/docs.yaml:{n}: Seite {e!r} doppelt")
    for p in sorted(pages - set(listed)):
        errs.append(f"docs/{p}.md:1: fehlt in website/data/docs.yaml")
    return errs


def check(root: Path = ROOT) -> list[str]:
    ps, errs = pairs(root)
    for de, en in ps:
        errs += compare(parse(de), parse(en), root)
    errs += check_nav(root)
    return errs


def main() -> None:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    errs = check(root)
    for e in errs:
        print(e)
    if errs:
        print(f"{len(errs)} Abweichung(en). Quelle ist Deutsch – die englische Fassung angleichen.", file=sys.stderr)
        sys.exit(1)
    print(f"Doku-Parität ok ({len(pairs(root)[0])} Paare).")


if __name__ == "__main__":
    main()
