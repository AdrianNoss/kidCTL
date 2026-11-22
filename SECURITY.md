# Security policy

**English** · [Deutsch](SECURITY.de.md)

KidCTL is parental control software: security vulnerabilities affect families and children. Thank you for reporting
them confidentially.

## Reporting a vulnerability

- **Preferred:** GitHub private vulnerability reporting – in the repository under **Security → "Report a
  vulnerability"** ([report directly](https://github.com/AdrianNoss/kidCTL/security/advisories/new)).
- **Alternatively:** email **security@kidctl.de**. No PGP.

> [!CAUTION]
> **Never** report security vulnerabilities as a public issue, pull request or in forums.

Helpful are: affected version and platform, steps to reproduce, the impact and – if you have one –
a suggested fix.

## Supported versions

Before version 1.0 only the **latest version** is supported. Fixes are released as a new patch version; there
are no backports. Keep server and agents on the same version.

| Version | Supported |
|---|---|
| latest version | yes |
| older versions | no |

## In scope

Please report confidentially:

- Privilege escalation, code execution, bypassing sign-in, TOTP or sessions, CSRF/XSS/injection in the web interface.
- Attacks on the agent ↔ server connection (device tokens, pairing, update signatures) and data leaks.
- In hosted operation (SaaS): access to other families' data.
- New ways in which a child **without admin rights** gets around the time limit, app control or web filter, or
  switches off enforcement.

## Out of scope

- Physical access without the [hardening](docs/hardening.md).
- VPN or fixed IP addresses.
- All gaps documented in [Limitations and known gaps](docs/limitations.md).
- Attacks that require admin or root rights on the child's computer.
- Missing best-practice headers without concrete impact.

## Process and timelines

1. **Acknowledgement** within 7 days.
2. **Initial assessment** (confirmed or not, severity) within 14 days.
3. **Fix** as soon as possible – target for severe vulnerabilities: 30 days.
4. **Coordinated disclosure:** publication after the fix release, at the latest after 90 days in agreement with
   you.

## Publication

- A **GitHub Security Advisory** for each confirmed vulnerability with affected and fixed version, CVSS score
  and a description without a ready-made exploitation guide – with thanks to you, if you wish.
- A **CVE** via GitHub from medium severity upwards.
- News and release notes link the advisory.
- **No bug bounty.**

## Testing rules

- Only test against **your own installation** or a test VM – never against `app.kidctl.de` or other third-party
  instances.
- Only use **your own child computers**.
- No DoS, no social engineering, no access to other people's data. If it happens anyway: stop immediately, don't
  store anything, report it.

## Safe harbor

We will not take legal action against anyone who researches and reports in good faith and according to these rules.
