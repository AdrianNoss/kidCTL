# Code-Signatur

[English](code-signing.md) · **Deutsch**

**Stand:** Die Windows-Installer sind derzeit **nicht code-signiert** (Authenticode). Ein Antrag bei der
SignPath Foundation wurde im November 2026 abgelehnt, weil das Projekt noch nicht bekannt genug ist; ein neuer
Antrag ist für später geplant. Bis dahin fragt Windows SmartScreen beim ersten Start nach.

## Wie die Windows-Installer gebaut werden

Der Windows-Installer `KidCTL-Setup-<Version>.exe` wird aus diesem Repository vom GitHub-Actions-Workflow
[`.github/workflows/release.yml`](../.github/workflows/release.yml) auf einem von GitHub bereitgestellten Runner
gebaut (`packaging/windows/build.py ci`). Jedes [Release](https://github.com/AdrianNoss/kidCTL/releases) nennt die
SHA-256 des Installers und verlinkt den Bau-Lauf. Der Workflow ist darauf vorbereitet, den Installer zum Signieren
einzureichen, sobald ein Signaturdienst verfügbar ist; signiert würden ausschließlich Bauten aus diesem Workflow.

Der Installer enthält Fremdkomponenten unverändert und nicht von uns signiert: die Embeddable-Distribution von
Python (signiert von der Python Software Foundation), Python-Wheels von PyPI und Unbound von NLnet Labs. Alle sind
mit SHA-256 in [`packaging/windows/vendor.lock`](../packaging/windows/vendor.lock) festgeschrieben.

Linux-Pakete werden getrennt mit dem eigenen GPG-Schlüssel des Projekts signiert (pacman-/APT-Repository);
automatische Windows-Updates prüft der Agent zusätzlich anhand einer Ed25519-Signatur von `latest.json`.

## Team und Rollen

| Rolle | Mitglieder |
|---|---|
| Committer und Reviewer | [Adrian Noss](https://github.com/AdrianNoss) |
| Freigabe | [Adrian Noss](https://github.com/AdrianNoss) |

Änderungen von außerhalb des Teams (Pull Requests) prüft ein Committer vor dem Zusammenführen. Releases gibt
jemand mit Freigaberolle von Hand frei. Alle Teammitglieder nutzen bei GitHub eine Mehr-Faktor-Anmeldung.

## Datenschutz

KidCTL überträgt keine Informationen an vernetzte Systeme außer an den KidCTL-Server, den die installierende
Person angibt (den eigenen, selbst betriebenen Server der Eltern). Der Agent auf dem Kinderrechner sendet
Nutzungszeiten, gesperrte Aktionen, die Liste installierter Programme, lokale Benutzernamen und Chat-Nachrichten an
diesen Server und holt von dort Regeln und Updates. Es gibt keine Telemetrie und keine Verbindung zu den
KidCTL-Entwicklern oder anderen Dritten. Die Sperrlisten des Website-Filters lädt der Server von den Anbietern, die
die Eltern ausgewählt haben.

Weil KidCTL eine Kindersicherung ist, wird es von einem Elternteil oder Administrator auf dem Rechner eines Kindes
installiert, mit Wissen des Kindes: Die Tray-App zeigt die Restzeit und die aktiven Regeln. Die Deinstallation
(Einstellungen → Apps, als Administrator) aktiviert alle von KidCTL gesperrten Konten wieder und entfernt Dienst,
Firewall-Regeln und Daten.

## Meldungen

Sicherheitsprobleme bitte vertraulich melden, wie in der [Sicherheitsrichtlinie](../SECURITY.de.md) beschrieben –
nie als öffentliches Issue.
