# Contributing

**English** · [Deutsch](CONTRIBUTING.de.md)

Thank you for your interest in KidCTL! Bug reports, ideas and pull requests are welcome.

## Process

1. **Issue first:** describe the bug or idea in an
   [issue](https://github.com/AdrianNoss/kidCTL/issues) before starting larger changes. This way we can clarify up
   front whether and how the change fits.
2. **Then a pull request** against `main`: one change per pull request, referencing the issue.
3. The automated checks (tests, linter, docs parity) must pass.

Requirements and changelog are in [`spec.md`](spec.md), conventions in [`CLAUDE.md`](CLAUDE.md).
Specification, conventions and code comments are in German.

## Tests

**New functionality must come with automated tests**; bug fixes should, where possible, include a test that
reproduces the bug. Pull requests without tests are usually not accepted.

Running the tests of the three components (venv with `server/requirements.txt` plus `pytest httpx dbus-next
websockets`; `PySide6` for the tray tests):

```bash
podman start kidctl-pg           # PostgreSQL for the server tests (dev/devserver.sh creates the container)
cd server && python -m pytest -q tests
cd agent  && python -m pytest -q tests
cd tray   && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```

The server tests and the agent's end-to-end tests need PostgreSQL (default: `127.0.0.1:55432`, user `kidctl`,
password `kidctl-dev`; override with `TEST_DATABASE_URL` or
`KIDCTL_E2E_DATABASE_URL`).

Linter (real errors only, no style rules; configured in `ruff.toml`):

```bash
ruff check
```

> [!WARNING]
> **Never** activate system-level parts such as PAM, nftables and mounts **on the development machine**; try them in
> the test VM instead: [`dev/vm/README.md`](dev/vm/README.md).

## Documentation

The guides in `docs/`, the README, the security policy and this file exist in German (source) and English with
**exactly the same content**. Always change both versions and check them:

```bash
python dev/check_docs_parity.py
```

## AI assistance

kidCTL itself is developed with AI (see [README, section “How kidCTL is built”](README.md#how-kidctl-is-built)).
Contributions made with AI assistance are welcome. The same rules apply: specification first, then code; tests; docs
in both languages. Whoever contributes has understood the contribution and stands behind it. Please mark AI
involvement in the commit (e.g. `Co-Authored-By`).

## Security vulnerabilities

> [!CAUTION]
> **Never** report security vulnerabilities as a public issue or pull request; report them confidentially as
> described in the [security policy](SECURITY.md).

## License

By contributing, you agree that your contribution is published under the project's license ([LICENSE](LICENSE)).
