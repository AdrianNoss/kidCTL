# Sicherheitsrichtlinie

[English](SECURITY.md) · **Deutsch**

KidCTL ist eine Kindersicherung: Sicherheitslücken betreffen Familien und Kinder. Danke, dass du sie vertraulich
meldest.

## Sicherheitslücke melden

- **Bevorzugt:** GitHub Private Vulnerability Reporting – im Repository unter **Security → „Report a
  vulnerability“** ([direkt melden](https://github.com/AdrianNoss/kidCTL/security/advisories/new)).
- **Alternativ:** E-Mail an **security@kidctl.de**. Kein PGP.

> [!CAUTION]
> Sicherheitslücken **nie** als öffentliches Issue, Pull Request oder in Foren melden.

Hilfreich sind: betroffene Version und Plattform, Schritte zum Nachvollziehen, die Auswirkung und – falls vorhanden –
ein Vorschlag zur Behebung.

## Unterstützte Versionen

Vor Version 1.0 wird nur die jeweils **neueste Version** unterstützt. Fixes erscheinen als neue Patch-Version, es
gibt keine Backports. Server und Agenten auf demselben Stand halten.

| Version | Unterstützt |
|---|---|
| neueste Version | ja |
| ältere Versionen | nein |

## Im Rahmen

Bitte vertraulich melden:

- Rechteausweitung, Codeausführung, Umgehen von Anmeldung, TOTP oder Sitzungen, CSRF/XSS/Injection im WebUI.
- Angriffe auf die Verbindung Agent ↔ Server (Geräte-Token, Kopplung, Update-Signaturen) und Datenabfluss.
- Im gehosteten Betrieb (SaaS): Zugriff auf Daten anderer Familien.
- Neue Wege, wie ein Kind **ohne Admin-Rechte** Zeitlimit, Programm- oder Website-Filter umgeht oder die
  Durchsetzung abschaltet.

## Nicht im Rahmen

- Physischer Zugriff ohne die [Härtung](docs/hardening.de.md).
- VPN bzw. fest eingetragene IP-Adressen.
- Alle in [Grenzen und bekannte Lücken](docs/limitations.de.md) dokumentierten Restlücken.
- Angriffe, die Admin- bzw. Root-Rechte auf dem Kinderrechner voraussetzen.
- Fehlende Best-Practice-Header ohne konkrete Auswirkung.

## Ablauf und Fristen

1. **Eingangsbestätigung** innerhalb von 7 Tagen.
2. **Erste Einschätzung** (bestätigt oder nicht, Schwere) innerhalb von 14 Tagen.
3. **Fix** so schnell wie möglich – Ziel bei schweren Lücken: 30 Tage.
4. **Coordinated Disclosure:** Veröffentlichung nach dem Fix-Release, spätestens nach 90 Tagen in Absprache mit
   dir.

## Veröffentlichung

- Für jede bestätigte Lücke ein **GitHub Security Advisory** mit betroffener und behobener Version, CVSS-Bewertung
  und einer Beschreibung ohne fertige Ausnutzungsanleitung – mit Dank an dich, wenn du das möchtest.
- Ab mittlerer Schwere eine **CVE** über GitHub.
- News und Release-Text verlinken das Advisory.
- **Kein Bug-Bounty.**

## Testregeln

- Nur an der **eigenen Installation** bzw. einer Test-VM testen – nie an `app.kidctl.de` oder anderen fremden
  Instanzen.
- Nur **eigene Kinderrechner** verwenden.
- Kein DoS, kein Social Engineering, kein Zugriff auf fremde Daten. Passiert es doch: sofort aufhören, nichts
  speichern, melden.

## Safe Harbor

Wer in gutem Glauben und nach diesen Regeln forscht und meldet, gegen den gehen wir nicht rechtlich vor.
