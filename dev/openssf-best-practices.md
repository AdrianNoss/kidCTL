# OpenSSF Best Practices (Passing) – vorbereitete Antworten

Zum Eintragen auf <https://www.bestpractices.dev/> (Projekt anlegen mit `https://github.com/AdrianNoss/kidCTL`).
Antworten auf Englisch zum Einfügen; „Met“ / „Unmet“ / „N/A“ wie im Formular. Danach die Projektnummer in
`website/hugo.toml` als `bestpractices_id` unter `[params]` eintragen. Dann zeigt die Startseite den Link, und der
Badge kann in die README.

Stand: 2026-11-20 (Version 0.43). Offen ist nur noch der Branch-Schutz (Ende des Dokuments).

## Basics

| Kriterium | Status | Antwort / Beleg |
|---|---|---|
| description_good | Met | README: "Parental control that stays at home: self-hosted for Windows and Linux." https://github.com/AdrianNoss/kidCTL#readme |
| interact | Met | GitHub Issues; project page https://kidctl.de/ |
| contribution | Met | "How to contribute: CONTRIBUTING.md (issue first, then pull request; tests; bilingual docs)." https://github.com/AdrianNoss/kidCTL/blob/main/CONTRIBUTING.md |
| contribution_requirements | Met | `CONTRIBUTING.md`: required tests, linter (`ruff check`), docs parity check; conventions in `CLAUDE.md`. |
| floss_license | Met | AGPL-3.0-or-later, `LICENSE` |
| floss_license_osi | Met | AGPL-3.0 is OSI-approved. |
| license_location | Met | `LICENSE` in the repository root. |
| documentation_basics | Met | Installation and use: https://kidctl.de/docs/ (sources in `docs/`). |
| documentation_interface | Met | Agent/server protocol in `common/kidctl_common/protocol.py` (versioned); CLI `kidctl --help`; WebUI documented in `docs/webui.md`. |
| sites_https | Met | GitHub, GitHub Pages and the package repository use HTTPS only. |
| discussion | Met | GitHub Issues (searchable, URL-addressable, no proprietary client needed). |
| english | Met | Code identifiers, docs (`docs/*.md`, `README.md`) and releases are available in English; German as second language. |
| maintained | Met | Regular releases (see GitHub Releases), issues are answered. |

## Change Control

| Kriterium | Status | Antwort / Beleg |
|---|---|---|
| repo_public | Met | https://github.com/AdrianNoss/kidCTL |
| repo_track | Met | Git history with author and date. |
| repo_interim | Met | Every change is committed to `main` between releases. |
| repo_distributed | Met | Git. |
| version_unique | Met | Version in `agent/kidctl_agent/__init__.py` and `packaging/arch/PKGBUILD`; CI checks both match and tags `v<version>`. |
| version_semver | Met | Semantic versioning (MAJOR.MINOR.PATCH). |
| version_tags | Met | Git tags `v<version>` created by `.github/workflows/release.yml`. |
| release_notes | Met | GitHub Releases with notes; human-readable news at https://kidctl.de/news/ |
| release_notes_vulns | Met | Security fixes are listed with a link to the GitHub Security Advisory (see SECURITY.md, news for 0.34.1). |

## Reporting

| Kriterium | Status | Antwort / Beleg |
|---|---|---|
| report_process | Met | GitHub Issues: https://github.com/AdrianNoss/kidCTL/issues |
| report_tracker | Met | GitHub Issues. |
| report_responses | Met | Bug reports in the last months were answered (see closed issues). |
| enhancement_responses | Met | Feature requests answered on the roadmap / issues. |
| report_archive | Met | Issues are public and archived by GitHub. |
| vulnerability_report_process | Met | `SECURITY.md` (private reporting via GitHub Security Advisories / e-mail). |
| vulnerability_report_private | Met | Private vulnerability reporting on GitHub enabled; e-mail address in `SECURITY.md`. |
| vulnerability_report_response | Met | Acknowledgement within 7 days, initial assessment within 14 days (`SECURITY.md`). |

## Quality

| Kriterium | Status | Antwort / Beleg |
|---|---|---|
| build | Met | Server: Docker (`docker compose up --build`); clients: `packaging/arch/PKGBUILD`, `.deb` and Windows installer built in `.github/workflows/release.yml`. |
| build_common_tools | Met | Docker, makepkg, dpkg, Inno Setup, Hugo. |
| build_floss_tools | Met | All build tools except Inno Setup are FLOSS (Inno Setup is free, source available). |
| test | Met | Automated test suites with pytest: `server/tests`, `agent/tests`, `tray/tests` (how to run: `CONTRIBUTING.md`). |
| test_invocation | Met | Standard invocation documented in `CONTRIBUTING.md`: `python -m pytest -q tests` in `server/`, `agent/` and `tray/`. |
| test_most | Met | Most code paths are covered; every change ships with tests (see `spec.md` changelog test notes). |
| test_continuous_integration | Met | GitHub Actions `.github/workflows/tests.yml` runs all test suites on every push to `main` and every pull request (server and agent against a PostgreSQL service container, tray with Qt offscreen). |
| test_policy | Met | `CONTRIBUTING.md`: "New functionality must come with automated tests." |
| tests_are_added | Met | Recent changes add tests (e.g. `server/tests/test_login_steps.py`, `test_invite_mail.py`). |
| tests_documented_added | Met | Test policy documented in `CONTRIBUTING.md` (section "Tests"). |
| warnings | Met | `ruff check` (Pyflakes `F`, syntax errors `E9`; `ruff.toml`) runs in `.github/workflows/tests.yml`. |
| warnings_fixed | Met | All findings were fixed when the linter was introduced; CI fails on new ones. |
| warnings_strict | Unmet | Optional (SUGGESTED): only error rules are enabled, no style rules. |

## Security

| Kriterium | Status | Antwort / Beleg |
|---|---|---|
| know_secure_design | Met | Threat model and security design in `spec.md` (Threat Model, Sicherheit), `docs/limitations.md`, `docs/hardening.md`. |
| know_common_errors | Met | CSRF protection, rate limiting, parameterized SQL (SQLAlchemy), output escaping (Jinja2 autoescape), CSP. |
| crypto_published | Met | Only standard algorithms: AES-GCM, HKDF, bcrypt, Ed25519, TOTP (RFC 6238), TLS. |
| crypto_call | Met | Uses `cryptography`, `bcrypt`, `pyotp`; no own crypto primitives. |
| crypto_floss | Met | All crypto libraries are FLOSS. |
| crypto_keylength | Met | AES-256, Ed25519, bcrypt (passlib default cost 12). |
| crypto_working | Met | No broken algorithms (no MD5/SHA-1 for security). |
| crypto_weaknesses | Met | — |
| crypto_pfs | Met | TLS via Traefik (TLS 1.2+/1.3 with ECDHE). |
| crypto_password_storage | Met | bcrypt (passlib), per-password salt. |
| crypto_random | Met | `secrets` module / `os.urandom`. |
| delivery_mitm | Met | Packages via HTTPS; Arch/Debian repositories GPG-signed; Windows updates Ed25519-signed (`latest.json`). |
| delivery_unsigned | Met | No hashes fetched over plain HTTP. |
| vulnerabilities_fixed_60_days | Met | Known vulnerabilities were fixed within days (see advisories). |
| vulnerabilities_critical_fixed | Met | — |
| no_leaked_credentials | Met | Secrets only in `.env` (not in the repo); GitHub push protection enabled. |

## Analysis

| Kriterium | Status | Antwort / Beleg |
|---|---|---|
| static_analysis | Met | GitHub CodeQL (default setup) on every push; OpenSSF Scorecard (`.github/workflows/scorecard.yml`); `pip-audit` for vendored Windows packages (`audit.yml`); Dependabot. |
| static_analysis_common_vulnerabilities | Met | CodeQL security queries. |
| static_analysis_fixed | Met | CodeQL alerts are fixed or dismissed with justification. |
| static_analysis_often | Met | On every push to `main`. |
| dynamic_analysis | Met | Monthly passive OWASP ZAP baseline and MDN HTTP Observatory scan of the public instance (`.github/workflows/websecurity.yml`). |
| dynamic_analysis_unsafe | N/A | Python (memory-safe). |
| dynamic_analysis_enable_assertions | Met | Tests run with assertions enabled (pytest). |
| dynamic_analysis_fixed | Met | Findings create an issue and are fixed. |

## Offene Punkte (vor dem Eintragen erledigen)

1. ~~`CONTRIBUTING.md`~~ – erledigt 2026-11-20 (`CONTRIBUTING.md`/`CONTRIBUTING.de.md`, im README verlinkt).
2. ~~Test-Workflow~~ – erledigt 2026-11-20 (`.github/workflows/tests.yml`). Erster Lauf erst nach dem Push sichtbar.
3. ~~Linter~~ – erledigt 2026-11-20 (`ruff check` mit `F`, `E9` in `tests.yml`, Konfiguration `ruff.toml`).
4. **Branch-Schutz für `main`** (Scorecard „Branch-Protection“) – in GitHub unter *Settings → Rules → Rulesets →
   New branch ruleset* (oder *Settings → Branches*), Ziel `main`:
   - *Restrict deletions* und *Block force pushes* an.
   - *Require status checks to pass*: die Jobs aus `tests.yml` („Linter und Doku“, „Server und Agent“,
     „Tray-App“) – erst auswählbar, nachdem der Workflow einmal gelaufen ist.
   - Optional *Require a pull request before merging*. Solange direkt auf `main` gepusht wird, den Admin als
     *Bypass* eintragen; sonst sperrt die Regel den eigenen Push.
   - Scorecard bewertet höher mit Pflicht-Review (≥ 1 Approval); bei einem einzelnen Maintainer nicht sinnvoll
     erfüllbar – bewusst offen lassen.
