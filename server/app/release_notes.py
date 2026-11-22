"""Kurzfassung der Änderungen je Version für Eltern (Hinweis „Neu in Version …“ auf der Übersicht, Info-Abschnitt).

Bei jedem Release oben einen Eintrag ergänzen: Version wie in ``agent/kidctl_agent/__init__.py`` (ein Test prüft das),
Datum, 2–4 kurze Stichpunkte je Sprache, für Eltern formuliert (nicht der technische Changelog). Der oberste Eintrag ist
die Version des laufenden Servers."""

RELEASES: list[dict] = [
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
