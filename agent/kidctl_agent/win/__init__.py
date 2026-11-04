"""Windows-Teil des Agenten (Windows 10 22H2 / 11 ab 23H2, x64). Nur ``ctypes`` und die Standardbibliothek –
keine nativen Zusatzpakete. Die Module laden die Windows-DLLs erst beim Aufruf, sodass sie sich unter Linux
importieren (und mit Attrappen testen) lassen."""
