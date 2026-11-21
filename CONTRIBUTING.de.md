# Mitmachen

[English](CONTRIBUTING.md) · **Deutsch**

Danke für dein Interesse an KidCTL! Fehlerberichte, Ideen und Pull Requests sind willkommen.

## Ablauf

1. **Zuerst ein Issue:** Beschreibe den Fehler oder die Idee in einem
   [Issue](https://github.com/AdrianNoss/kidCTL/issues), bevor du größere Änderungen beginnst. So lässt sich vorab
   klären, ob und wie die Änderung passt.
2. **Dann ein Pull Request** gegen `main`: eine Änderung pro Pull Request, mit Verweis auf das Issue.
3. Die automatischen Prüfungen (Tests, Linter, Doku-Parität) müssen grün sein.

Fachliche Anforderungen und Changelog stehen in [`spec.md`](spec.md), Konventionen in [`CLAUDE.md`](CLAUDE.md).
Spezifikation, Konventionen und Code-Kommentare sind deutsch.

## Tests

**Neue Funktionen brauchen automatisierte Tests**, Fehlerbehebungen nach Möglichkeit einen Test, der den Fehler
nachstellt. Pull Requests ohne Tests werden in der Regel nicht übernommen.

Tests der drei Komponenten starten (venv mit `pip install --require-hashes -r .github/requirements/tests.txt`;
für die Tray-Tests `PySide6`):

```bash
podman start kidctl-pg           # PostgreSQL für die Server-Tests (dev/devserver.sh legt den Container an)
cd server && python -m pytest -q tests
cd agent  && python -m pytest -q tests
cd tray   && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```

Die Server-Tests und die End-to-End-Tests des Agenten brauchen PostgreSQL (Standard: `127.0.0.1:55432`, Benutzer
`kidctl`, Passwort `kidctl-dev`; abweichend per `TEST_DATABASE_URL` bzw.
`KIDCTL_E2E_DATABASE_URL`).

Linter (nur echte Fehler, keine Stilregeln; Konfiguration in `ruff.toml`):

```bash
ruff check
```

> [!WARNING]
> Systemnahe Teile wie PAM, nftables und Mounts **nie auf dem Entwicklungsrechner** scharf schalten, sondern in der
> Test-VM ausprobieren: [`dev/vm/README.md`](dev/vm/README.md).

## Doku

Die Anleitungen in `docs/`, das README, die Sicherheitsrichtlinie und diese Datei gibt es auf Deutsch (Quelle) und
Englisch mit **exakt gleichem Inhalt**. Ändere immer beide Fassungen und prüfe sie:

```bash
python dev/check_docs_parity.py
```

## KI-Unterstützung

kidCTL wird selbst mit KI entwickelt (siehe [README, Abschnitt „Wie kidCTL entsteht“](README.de.md#wie-kidctl-entsteht)).
Beiträge, die mit KI-Unterstützung entstanden sind, sind willkommen. Für sie gelten dieselben Regeln: erst
Spezifikation, dann Code; Tests; Doku in beiden Sprachen. Wer beiträgt, hat den Beitrag verstanden und steht für ihn
ein. KI-Mitarbeit bitte im Commit kennzeichnen (z. B. `Co-Authored-By`).

## Sicherheitslücken

> [!CAUTION]
> Sicherheitslücken **nie** als öffentliches Issue oder Pull Request melden, sondern vertraulich wie in der
> [Sicherheitsrichtlinie](SECURITY.de.md) beschrieben.

## Lizenz

Mit einem Beitrag stimmst du zu, dass er unter der Lizenz des Projekts veröffentlicht wird ([LICENSE](LICENSE)).
