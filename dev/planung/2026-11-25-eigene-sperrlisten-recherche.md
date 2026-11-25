# Eigene Sperrlisten für Eltern – Machbarkeit

Stand 2026-11-25, Basis `48b763e` (0.47.0). Nur Recherche, nichts umgesetzt.

**Frage:** Können Eltern eigene Sperrlisten (ggf. begrenzte Anzahl) hinterlegen und für ihre Kinder nutzen?

**Kurzantwort:** Ja, gut machbar und überwiegend serverseitig. Der Listen-Mechanismus im Protokoll ist bereits
generisch (`BlocklistRef` mit ID + SHA-256, Abruf über `/api/v1/lists/<id>`); eigene Listen lassen sich als weitere
IDs ausliefern – **ohne Protokolländerung und ohne Agent-Änderung**, auch alte Agents (Linux und Windows) können sie
sofort nutzen.

## Ist-Stand

| Baustein | Heute |
|---|---|
| Modi je Kind | Aus / Whitelist / Blacklist (`child.web_mode`) |
| Eigene Domains | `child_domain` (allow/deny) je Kind, Textfeld im WebUI, **kein Mengenlimit**, Spalte unverschlüsselt |
| Öffentliche Listen | 9 fest im Code (`webfilter.BLOCKLISTS`: HaGeZi, OISD, StevenBlack), Auswahl je Kind (`child_blocklist`) |
| Abruf | Server lädt benutzte Listen täglich (`_refresh_lists_forever`) und sofort bei erster Auswahl; `parse_domains` versteht Domain-, hosts- und Adblock-Format; max. 64 MB je Download; Datei `<data>/lists/<id>.txt`, Stand in `blocklist_state` (plattformweit, ohne Familie) |
| Verteilung | Policy enthält `WebFilter.lists = [BlocklistRef(id, sha256, name, report)]`; `deny`/`allow` stehen **direkt in der Policy** (WebSocket, Agent `max_size` 4 MB) |
| Agent | `fetch_lists` (gemeinsam Linux/Windows): holt Liste per Geräte-Token, prüft SHA-256, Cache unter `lists/<id>.txt` → offline nutzbar; `build_zones` baut Unbound-Zonen (`always_nxdomain`), `report_sources` fürs Sperrprotokoll („Sperrliste <name>“) |
| Prüfungen | Listen-ID `^[a-z0-9][a-z0-9-]{0,63}$` (Dateiname), Domains nur als gültige Hostnamen in Unbound, Agent-Download ohne Weiterleitungen |
| Ressourcen | ≈ 440 Byte RAM je Domain und Kind (Vorauswahl 250 000 Domains ≈ 110 MB; Linux ein Unbound je Kind) |
| Download-Endpunkt | prüft nur, ob das Geräte-Token gültig ist – **nicht**, ob die Liste zur Familie gehört (bei öffentlichen Listen egal) |

Nebenbefund: Das bestehende Feld „Gesperrte Domains“ hat kein Limit. Sehr große Eingaben landen vollständig in der
Policy; über 4 MB würde der Agent die Policy-Nachricht ablehnen (Verbindungsabbruch, Gerät bleibt auf alter Policy).
Unabhängig von diesem Feature sinnvoll: Limit z. B. 1 000 Einträge je Feld.

## Varianten

### (a) Eigene Liste im WebUI pflegen (Name + Domains)

Familie legt benannte Listen an (z. B. „Social Media“, „Spieleseiten“), Inhalt als Textfeld; je Kind per Kachel
auswählbar wie die öffentlichen Listen.

- **Server:** neue Tabellen `family_blocklist` (Name verschlüsselt, Inhalt, SHA-256, Anzahl, geändert) und Zuordnung
  je Kind (bestehende `child_blocklist` mit ID `own-<id>` oder eigene Tabelle); Seite „Eigene Sperrlisten“ unter
  Internet bzw. Familie; `web_policy` erzeugt `BlocklistRef(id="own-<id>", sha256=…, name=…)`; Download-Endpunkt für
  `own-*`: Familie des Geräts setzen, Zugehörigkeit prüfen, sonst 404. Migration. Aufwand **S–M**.
- **Protokoll/Agent:** keine Änderung. Agent cacht die Liste wie jede andere, offline nutzbar, ETag-Abgleich.
- **Größe:** Inhalt nicht in der Policy, nur Referenz → Policy bleibt klein, Agent lädt nur bei Änderung.
- **Aktualisierung:** sofort beim Speichern (Push der Policies betroffener Kinder, wie heute).
- **Sicherheit:** Inhalt ist Familiendaten → mit Familienschlüssel verschlüsselt speichern (Feld, nicht Datei);
  Endpunkt entschlüsselt im Familienkontext. Agent-seitige Prüfungen greifen unverändert.
- **Vorteil gegenüber dem vorhandenen Feld „Gesperrte Domains“:** einmal pflegen, mehreren Kindern zuweisen; eigener
  Name im Sperrprotokoll; nicht Teil der Policy.

### (b) Abo einer externen Liste per URL

Eltern geben eine URL an (hosts/Adblock/Domains), der Server lädt sie regelmäßig.

- **Abruf durch den Server** (empfohlen, falls überhaupt): normalisiert zentral mit `parse_domains`, Agent bleibt
  unverändert, Agent lädt nur vom eigenen Server (Token nie an Dritte).
  - **SSRF-Schutz Pflicht** (SaaS, öffentlich erreichbar): nur `https`, Standardport; Hostname auflösen und private,
    Loopback-, Link-Local-, CGNAT-, Multicast- und Metadaten-Adressen (v4/v6) ablehnen; Verbindung an die geprüfte IP
    binden (kein DNS-Rebinding); Weiterleitungen nur einzeln neu geprüft (max. 3) oder gar nicht; Größenlimit
    (z. B. 10 MB) beim Streamen, Timeout, keine Cookies/Auth-Header. Kein Rückkanal: Eltern sehen nur
    „ok / Fehler / Anzahl“, nie den Antworttext (sonst Lesezugriff auf interne Dienste).
  - **Last:** täglicher Abruf je URL, gleiche URLs familienübergreifend nur einmal laden (Cache nach URL-Hash),
    `If-None-Match`/`If-Modified-Since`. Bei 5 000 Familien × wenige Listen unkritisch, aber Fehlerzähler und
    Abschalten nach n Fehlversuchen nötig.
  - **Datenschutz:** URL verrät ggf. Interessen → verschlüsselt speichern; Abruf verrät der Gegenstelle die
    Server-IP (kein Personenbezug).
  - Aufwand zusätzlich zu (a): **M** (SSRF-Guard + Tests, Abrufplanung, Fehleranzeige).
- **Abruf durch den Agenten:** verworfen. Widerspricht „Server steuert nur die Policy“ (ein übernommener Server
  könnte Geräte beliebige Adressen im Heimnetz abfragen lassen), doppelte Logik Linux/Windows, Normalisierung und
  Limits auf jedem Gerät, Protokolländerung nötig.
- **Alternative ohne SSRF-Risiko:** Betreiber nimmt gewünschte öffentliche Listen in den Katalog `BLOCKLISTS` auf
  (Wunsch per Support-Ticket). Deckt den häufigsten Fall („ich will Liste X von HaGeZi“) ab.

### (c) Datei-Import

Upload einer Datei (hosts/Adblock/Domains) in die eigene Liste aus (a), Parser `parse_domains` existiert.

- Aufwand gering (**S**, Formularfeld + Limit + Fehlermeldung „n ungültige Zeilen ignoriert“); kein SSRF, keine
  Abrufplanung; Aktualisierung nur manuell.
- Upload-Limit vor dem Parsen (z. B. 5 MB), damit der Server nicht große Dateien im Speicher hält.

## Bewertung

| | (a) WebUI | (b) URL-Abo (Server) | (c) Import |
|---|---|---|---|
| Nutzen | hoch (eigene Kategorien) | mittel (Power-User) | mittel |
| Aufwand Server | S–M | +M | +S |
| Protokoll / Agent | keine / keine | keine / keine | keine / keine |
| Alte Agents | funktionieren | funktionieren | funktionieren |
| Sicherheitsrisiko | gering | **SSRF**, Fremdinhalte | gering |
| Aktualisierung | beim Speichern | täglich automatisch | manuell |

## Empfehlung

1. **(a) + (c) zusammen umsetzen** als „Eigene Sperrlisten“ je Familie, zuweisbar je Kind im Blacklist-Modus,
   ausgeliefert als `own-<id>` über den bestehenden Listen-Mechanismus. Download-Endpunkt um Familienprüfung
   erweitern, Inhalt verschlüsselt.
2. **(b) zunächst nicht** – stattdessen Katalog öffentlicher Listen bei Bedarf erweitern. URL-Abo nur, wenn es
   wirklich nachgefragt wird; dann serverseitig mit SSRF-Guard.
3. **Limits** (Standardwerte, per `.env` änderbar, damit Self-Hoster höher gehen können):
   - max. **5 eigene Listen je Familie**,
   - max. **20 000 Einträge je Liste** (≈ 9 MB RAM je Kind und Liste), Upload max. 5 MB,
   - zusätzlich bestehendes Feld „Gesperrte/Freigegebene Domains“ auf **1 000 Einträge** begrenzen (Nebenbefund).
4. Sperrprotokoll: eigene Listen standardmäßig mit `report=True` (Eltern wollen Treffer sehen).
5. Restlücke „Ausnahme gilt nicht für spezifischere Listeneinträge“ gilt auch für eigene Listen – in der Hilfe
   erwähnen, keine neue Restlücke.

## Offene Fragen an Adrian

1. Gelten eigene Listen **je Familie** (mehreren Kindern zuweisbar, empfohlen) oder je Kind?
2. Auch **eigene Freigabelisten** für den Whitelist-Modus (z. B. „Schulseiten“)? Technisch gleicher Weg
   (`allow` steht heute aber in der Policy, bräuchte für große Listen eine Listen-Referenz → Protokolländerung).
3. URL-Abo (b) jetzt, später oder gar nicht? Alternative: Katalog öffentlicher Listen erweitern.
4. Limits 5 Listen / 20 000 Einträge passend? Unterschiedliche Grenzen je Tarif (SaaS) gewünscht?
5. Nebenbefund: Limit 1 000 für das bestehende Domain-Feld als eigener kleiner Fix vorziehen?

## Etappe / Version

- Eigene Etappe nach 0.49 (**0.50**), oder als kleiner Zusatz an 0.49, da kein Agent-Release nötig ist.
- Umfang: Modell + Alembic-Migration (Alt-Schema-Test), Seite „Eigene Sperrlisten“ (anlegen, bearbeiten, Import,
  löschen; CSRF), Kacheln im Internet-Tab, Endpunkt mit Familienprüfung + Tests (fremde Familie → 404, beide
  Agent-Formate), Limits, Familienexport/-löschung berücksichtigen, Screenshots, Doku DE/EN, README, Funktionsliste,
  Roadmap, News (Minor-Version), Release Notes.
- Smoke-Test: Liste anlegen → Kind zuweisen → Agent (Linux-VM und Windows-VM) lädt `own-<id>`, Domain gesperrt,
  Sperrprotokoll zeigt Listennamen; Liste ändern → neuer Hash → Agent lädt nach; offline bleibt Sperre aktiv.
