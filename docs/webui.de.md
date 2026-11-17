# Einstellungen im WebUI

Je Kind (Seite des Kindes):

- **Bildschirmzeit**: Wochenvorlage mit Tageskontingent und erlaubtem Zeitfenster (Werktage und
  Wochenende oder jeder Tag einzeln).
- **Spiele-Limit** („davon Spiele“ in der Wochenvorlage): höchstens so viele Minuten Spiele am Tag
  (Steam, Heroic und Programme der Gruppe „Spiele“, nicht die Launcher; siehe [Spiele](games.de.md)). Vorwarnung 5 und 1 Minute
  vorher, danach werden Spiele geschlossen; der restliche Rechner bleibt nutzbar. Leer = kein Limit.
  Weitere Apps zählen als Spiel, wenn in der Programmliste das Kästchen „Spiel“ neben der App angehakt ist
  (z. B. Roblox oder der Minecraft-Launcher; Apps der Gruppe „Spiele“ sind immer Spiele).
- **Zeit gutschreiben**: zusätzliche Minuten, ein längeres Zeitfenster oder zusätzliche Spielzeit für
  ein bestimmtes Datum. Verlängerungs- und Spielzeit-Anfragen aus der Tray-App lassen sich auch direkt
  im Chat genehmigen.
- **Programme**:
  - **Aus**
  - **Nur protokollieren**: zum Einstieg empfohlen, um zu sehen, was genutzt wird.
  - **Sperren**: nur freigegebene Programme. Der Katalog füllt sich mit dem, was der Rechner
    meldet. Steam wird nur als Ganzes freigegeben.
- **Internet** (Website-Filter):
  - **Aus**
  - **Whitelist**: nur Freigegebenes. Dafür gibt es Bundles wie YouTube, Wikipedia, Lern-Apps oder
    Microsoft 365.
  - **Blacklist**: Sperrlisten, z. B. HaGeZi Multi + NSFW, plus eigene Sperren und Ausnahmen.
  - SafeSearch je Kind.
- **Statistik**: Nutzung der letzten 14 Tage und Zeit pro Programm.
- **Freigabe-Anfragen**: Wird ein Programm gesperrt, hat die Meldung beim Kind den Knopf
  „Freigabe anfragen“. Gesperrte Websites fragt das Kind über das Tray-Menü „Gesperrt – Freigabe
  anfragen …“ an. Die Anfrage erscheint im Chat (mit Push) mit **Dauerhaft erlauben**, **Nur heute**
  (gilt bis Mitternacht) oder **Ablehnen**. Dauerhafte Freigaben stehen danach in der Programm- bzw.
  Website-Liste und lassen sich dort zurücknehmen.

Je Gerät: Alarm, wenn das Gerät länger als X Stunden offline ist (Standard 48 h).

## Sprache

KidCTL spricht Deutsch und Englisch. Eltern wählen die Sprache im Profil (Standard: Browsersprache).
Auf dem Kinderrechner gilt die Sprache der Desktop-Sitzung des Kindes (Plasma: Systemeinstellungen →
Region & Sprache; GNOME: Einstellungen → System → Region & Sprache), vor der Anmeldung unter GNOME
die dort gewählte Sprache, sonst die Systemsprache aus `/etc/locale.conf`. Alles, was nicht mit `de`
beginnt, ergibt Englisch.
