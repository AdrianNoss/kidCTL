"""IMAP-Zugriff (nur TLS): ungelesene Mails lesen, als gelesen markieren, in einen Ordner verschieben.
Löscht nie endgültig außer dem für ein MOVE ohne MOVE-Erweiterung nötigen EXPUNGE der Quellkopie."""

from __future__ import annotations

import imaplib
import ssl


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mailbox:
    def __init__(self, host: str, port: int, user: str, password: str, folder: str = "INBOX", factory=None):
        factory = factory or (lambda: imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context()))
        self.imap = factory()
        self.imap.login(user, password)
        self.folder = folder
        typ, _ = self.imap.select(_quote(folder))
        if typ != "OK":
            raise RuntimeError(f"IMAP-Ordner {folder!r} nicht auswählbar")
        self._caps = {c.decode().upper() if isinstance(c, bytes) else str(c).upper()
                      for c in (getattr(self.imap, "capabilities", ()) or ())}
        self._ensured: set[str] = set()

    def unseen(self) -> list[tuple[bytes, bytes]]:
        typ, data = self.imap.uid("SEARCH", None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return []
        out = []
        for uid in data[0].split():
            typ, msg = self.imap.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: erst nach Verarbeitung „gelesen“
            if typ == "OK" and msg and isinstance(msg[0], tuple):
                out.append((uid, msg[0][1]))
        return out

    def _ensure(self, folder: str) -> None:
        if folder not in self._ensured:
            self.imap.create(_quote(folder))  # Fehler „existiert schon“ ist egal
            self._ensured.add(folder)

    def move(self, uid: bytes, folder: str) -> None:
        self._ensure(folder)
        self.imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        if "MOVE" in self._caps:
            typ, _ = self.imap.uid("MOVE", uid, _quote(folder))
            if typ == "OK":
                return
        typ, _ = self.imap.uid("COPY", uid, _quote(folder))
        if typ != "OK":
            raise RuntimeError(f"IMAP: Verschieben nach {folder!r} fehlgeschlagen")
        self.imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        self.imap.expunge()

    def close(self) -> None:
        try:
            self.imap.logout()
        except Exception:
            pass
