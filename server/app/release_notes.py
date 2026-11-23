"""Kurzfassung der Änderungen je Version für Eltern (Hinweis „Neu in Version …“ auf der Übersicht, Info-Abschnitt).

Bei jedem Release oben einen Eintrag ergänzen: Version wie in ``agent/kidctl_agent/__init__.py`` (ein Test prüft das),
Datum, 2–4 kurze Stichpunkte je Sprache, für Eltern formuliert (nicht der technische Changelog). Der oberste Eintrag ist
die Version des laufenden Servers."""

RELEASES: list[dict] = [
    {
        "version": "0.46.1",
        "date": "2026-11-23",
        "de": [
            "Linux: Im Modus „Nur protokollieren“ starten Programme aus dem Home- und Spieleordner wieder; "
            "gesperrt wird nur noch im Modus „Sperren“.",
            "Aktualisierte Server-Bibliotheken.",
        ],
        "en": [
            "Linux: in “Monitor only” mode, programs from the home and game folders start again; "
            "blocking only happens in “Block” mode.",
            "Updated server libraries.",
        ],
    },
    {
        "version": "0.46.0",
        "date": "2026-11-22",
        "de": [
            "Update per Knopf: Auf der Geräteseite lässt sich kidCTL auf dem Kinderrechner direkt aktualisieren "
            "(einmalig muss jeder Rechner noch von Hand auf 0.46 gebracht werden).",
            "Unter Einstellungen → Info stehen Version und Links; auch die Tray-App hat einen Punkt „Info“.",
            "Nach jedem Update zeigt die Übersicht einmalig, was neu ist.",
        ],
        "en": [
            "Update button: kidCTL on a child computer can be updated right from the device page "
            "(each computer has to be brought to 0.46 by hand once).",
            "Settings → About shows the version and links; the tray app has an “About” entry as well.",
            "After each update, the overview shows once what is new.",
        ],
    },
    {
        "version": "0.45.2",
        "date": "2026-11-22",
        "de": [
            "Sicherheitsupdate für den Webfilter unter Linux – bitte die Agenten aktualisieren.",
            "Die Programme eines Kindes erscheinen als Kacheln, Spiele gesammelt unter „Spiele“.",
            "Die Tray-App unter Windows zeigt das KidCTL-Symbol in Titel- und Taskleiste.",
        ],
        "en": [
            "Security update for the web filter on Linux – please update the agents.",
            "A child's programs are shown as tiles, games are grouped under “Games”.",
            "The tray app on Windows shows the KidCTL icon in the title bar and taskbar.",
        ],
    },
]
