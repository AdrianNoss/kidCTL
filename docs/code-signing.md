# Code signing policy

**English** · [Deutsch](#deutsch)

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

## What is signed

Only the Windows installer `KidCTL-Setup-<version>.exe`. It is built from this repository by the
GitHub Actions workflow [`.github/workflows/windows.yml`](../.github/workflows/windows.yml) on a
GitHub-hosted runner (`packaging/windows/build.py ci`) and submitted to SignPath from there. Builds from
other machines are never signed.

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

Changes from anyone outside the team (pull requests) are reviewed by a committer before merging. Every
signing request is approved manually in SignPath. All team members use multi-factor authentication for
GitHub and SignPath.

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

Security issues or misuse of the signing certificate: please open an issue on
[GitHub](https://github.com/AdrianNoss/kidCTL/issues) or contact the maintainer listed above.

---

## Deutsch

Kostenlose Code-Signatur durch [SignPath.io](https://about.signpath.io/), Zertifikat von der
[SignPath Foundation](https://signpath.org/).

- **Signiert wird** nur der Windows-Installer, gebaut in GitHub Actions aus diesem Repository und von
  dort an SignPath übergeben; jede Signatur wird von Hand freigegeben.
- **Team:** Adrian Noss (Committer, Reviewer, Freigabe); alle mit Zwei-Faktor-Anmeldung bei GitHub und
  SignPath.
- **Datenschutz:** KidCTL überträgt Daten ausschließlich an den KidCTL-Server, den die Eltern bei der
  Installation angeben (eigener Server). Keine Telemetrie, keine Verbindung zu den Entwicklern.
