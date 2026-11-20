---
title: "So entsteht kidCTL"
slug: "how-kidctl-is-built"
translationKey: "how-built"
description: "kidCTL wird mit KI entwickelt und von einem Menschen verantwortet – wer was macht und welche Regeln gelten."
---

KidCTL wird von Adrian Noss **mit Hilfe von KI entwickelt**. Den größten Teil von Code, Tests, Übersetzungen und
Anleitungen schreibt [Claude Code](https://claude.com/claude-code), ein KI-Programmierwerkzeug von Anthropic. Die
Verantwortung für kidCTL trägt ein Mensch. Diese Seite erklärt, wie das zusammenpasst.

## Wer macht was

**Adrian** legt fest, was kidCTL können soll und was nicht. Jede neue Funktion wird vorher in Rückfragen abgestimmt,
bis die Anforderungen klar sind. Adrian testet auf echten Geräten mit echten Spielen und Launchern, gibt jede Version
frei und veröffentlicht sie. Signaturschlüssel, Server und Releases liegen nur in seiner Hand.

**Claude Code** setzt um, was abgestimmt ist: Code, automatische Tests, Übersetzungen, Anleitungen und Screenshots.
Die KI arbeitet nur in der Entwicklungsumgebung mit Testdaten – sie hat keinen Zugriff auf Live-Server, echte
Familiendaten oder Schlüssel.

## Feste Regeln

- **Erst beschreiben, dann bauen.** Jede Änderung steht zuerst in der Spezifikation
  ([`spec.md`](https://github.com/AdrianNoss/kidCTL/blob/main/spec.md)) und wird dort mit Datum festgehalten –
  zusammen mit dem, was getestet wurde.
- **Keine Funktion ohne Tests.** Mehrere hundert automatische Tests laufen vor jeder Version; neue Funktionen bringen
  ihre Tests mit.
- **Systemnahes zuerst in der Test-VM.** Anmeldung, Firewall und Laufwerksfreigaben werden in virtuellen Maschinen
  (Arch Linux mit KDE Plasma und GNOME, Windows 11) ausprobiert, nie ungeprüft auf einem echten Rechner.
- **Kleine Schritte.** Größere Vorhaben werden in Etappen umgesetzt; jede Etappe ist eine eigene, nachvollziehbare
  Version.
- **Offene Regeln.** Die Arbeitsanweisungen für die KI stehen öffentlich im Repository
  ([`CLAUDE.md`](https://github.com/AdrianNoss/kidCTL/blob/main/CLAUDE.md)). Commits, an denen die KI mitgeschrieben
  hat, sind als solche gekennzeichnet.
- **Automatische Prüfungen.** CodeQL, OpenSSF Scorecard und ein monatlicher Scan der öffentlichen Instanz prüfen
  laufend mit – die Ergebnisse stehen auf der Startseite.

## Was das für dich bedeutet

- **kidCTL selbst nutzt keine KI.** Die Software schickt nichts an KI-Dienste und wertet nichts mit KI aus. Die KI
  ist ein Werkzeug beim Bauen, nicht Teil des Produkts.
- **Deine Daten bleiben bei dir.** Daran ändert die Entwicklungsweise nichts: kidCTL spricht nur mit deinem eigenen
  Server, ohne Telemetrie.
- **Alles ist nachprüfbar.** Der Quelltext ist frei (AGPL-3.0); jede Änderung ist mit Begründung in Git und in der
  Spezifikation nachzulesen.

## Was es noch nicht gibt

Ein **unabhängiges Sicherheits-Audit durch Menschen** hat noch nicht stattgefunden. Automatische Prüfungen ersetzen
das nicht. Bekannte Grenzen und Umgehungsmöglichkeiten stehen offen in den [Grenzen](../docs/limitations/).
Sicherheitslücken bitte vertraulich melden: [Sicherheit](../docs/security/).

## Mitmachen

Beiträge sind willkommen – auch solche, die mit KI-Unterstützung entstanden sind. Für sie gelten dieselben Regeln:
Spezifikation, Tests, Doku in beiden Sprachen. Wer beiträgt, steht für seinen Beitrag ein.
