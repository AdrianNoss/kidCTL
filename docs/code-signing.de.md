# Code-Signatur

[English](code-signing.md) · **Deutsch**

- **Stand:** Die Windows-Installer sind derzeit **nicht signiert**. Ein Antrag bei der SignPath Foundation
  wurde im November 2026 abgelehnt, weil das Projekt noch nicht bekannt genug ist; ein neuer Antrag ist
  für später geplant. Bis dahin fragt Windows SmartScreen beim ersten Start nach.
- **Bau:** Der Windows-Installer entsteht in GitHub Actions aus diesem Repository; jedes Release nennt die
  SHA-256 und verlinkt den Bau-Lauf.
- **Team:** Adrian Noss (Committer, Reviewer, Freigabe); alle mit Zwei-Faktor-Anmeldung bei GitHub.
- **Datenschutz:** KidCTL überträgt Daten ausschließlich an den KidCTL-Server, den die Eltern bei der
  Installation angeben (eigener Server). Keine Telemetrie, keine Verbindung zu den Entwicklern.
