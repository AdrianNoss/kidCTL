# Code signing policy

**English** · [Deutsch](code-signing.de.md)

**Status:** The Windows installers are currently **not code-signed** (Authenticode). An application to
the SignPath Foundation was declined in November 2026 because the project is not yet well known; we
plan to apply again later. Until then, Windows SmartScreen asks for confirmation on first launch.

## How Windows installers are built

The Windows installer `KidCTL-Setup-<version>.exe` is built from this repository by the GitHub Actions
workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) on a GitHub-hosted runner
(`packaging/windows/build.py ci`). Each [release](https://github.com/AdrianNoss/kidCTL/releases) lists
the installer's SHA-256 and links the build run. The workflow is prepared to submit the installer for
signing once a signing service is available; only builds from this workflow would ever be signed.

The installer bundles third-party components unchanged and unsigned by us: the embeddable Python
distribution (signed by the Python Software Foundation), Python wheels from PyPI and Unbound from
NLnet Labs. All are pinned with SHA-256 in [`packaging/windows/vendor.lock`](../packaging/windows/vendor.lock).

Linux packages are signed separately with the project's own GPG key (pacman/apt repository); Windows
auto-updates are additionally verified by the agent with an Ed25519 signature of `latest.json`.

## Team and roles

| Role | Members |
|---|---|
| Committers and reviewers | [Adrian Noss](https://github.com/AdrianNoss) |
| Approvers | [Adrian Noss](https://github.com/AdrianNoss) |

Changes from anyone outside the team (pull requests) are reviewed by a committer before merging.
Releases are approved manually by an approver. All team members use multi-factor authentication for
GitHub.

## Privacy

KidCTL does not transfer any information to networked systems other than the KidCTL server that the
person installing it specifies (the parents' own, self-hosted server). The agent on the child's
computer sends usage times, blocked actions, the list of installed programs, local user names and
chat messages to that server and fetches rules and updates from it. There is no telemetry and no
connection to the KidCTL developers or other third parties. The web filter's block lists are downloaded
by the server from the list providers selected by the parents.

Because KidCTL is parental-control software, it is meant to be installed by a parent or administrator
on a child's computer, with the child's knowledge: the tray app shows the remaining time and the
active rules. Uninstalling (Settings → Apps, as administrator) re-enables all accounts that KidCTL
locked and removes the service, firewall rules and data.

## Reporting

Security issues: please report them confidentially as described in the [security policy](../SECURITY.md) –
never as a public issue.
