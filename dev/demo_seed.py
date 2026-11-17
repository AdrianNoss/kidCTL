#!/usr/bin/env python3
"""Fiktive Demo-Daten für Screenshots der Projektseite (dev/screenshots.py) – nie gegen echte Server.

Spricht nur die echten Schnittstellen an: Eltern-Aktionen als HTTP-Formulare (wie dev/webctl.py), die Kinderrechner
als simulierte Agenten über Kopplung + WebSocket (Protokoll v1). Nur Nutzungszeiten älter als 7 Tage – die der
Agent nicht mehr melden darf – schreibt das Skript direkt in die Datenbank (PostgreSQL, ``--database-url``).

Inhalt: Kinder „Lena“ und „Max“, Rechner Linux (Lena) und Windows (Familien-PC, beide Kinder), 14 Tage Nutzung mit
Spielen, Programme, Website-Filter, gesperrte Aktionen, Chat, offene und beantwortete Anfragen. Texte je Sprache.

    dev/demo_seed.py --url http://127.0.0.1:8010 --database-url postgresql+psycopg://… --secret TOTP --lang de
        legt alles an und hält die Agenten verbunden (Strg+C beendet); als Modul: seed() + Agent.close().
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
import pyotp
import websockets
from sqlalchemy import create_engine, text

PASSWORD = "demo-passwort-lang"
PARENT = {"de": "mama", "en": "mom"}

T = {
    "de": {
        "laptop": "Lenas Laptop", "pc": "Familien-PC",
        "lena_full": "Lena", "max_full": "Max",
        "ext_reason": "Ich bin gleich mit dem Referat fertig, brauche noch eine halbe Stunde.",
        "approve_reply": "Okay, aber danach ist Schluss für heute.",
        "chat_lena": "Kann ich am Wochenende mit Paula Minecraft spielen?",
        "chat_reply": "Am Samstag nach dem Mittagessen, ja.",
        "games_reason": "Wir sind fast im letzten Level!",
        "access_reason": "Brauche ich für Kunst.",
        "chat_max": "Ist Scratch freigegeben? Wir sollen in Informatik ein Spiel bauen.",
    },
    "en": {
        "laptop": "Lena's laptop", "pc": "Family PC",
        "lena_full": "Lena", "max_full": "Max",
        "ext_reason": "I'm almost done with my presentation, I need another half hour.",
        "approve_reply": "Okay, but that's it for today.",
        "chat_lena": "Can I play Minecraft with Paula at the weekend?",
        "chat_reply": "On Saturday after lunch, yes.",
        "games_reason": "We're almost at the last level!",
        "access_reason": "I need it for art class.",
        "chat_max": "Is Scratch allowed? We have to build a game in computer science.",
    },
}

# Programmkatalog je Gerät (Schlüssel wie vom echten Agenten)
LINUX_APPS = [
    ("exe:/usr/lib/firefox/firefox", "Firefox", "Network;WebBrowser;"),
    ("exe:/usr/bin/libreoffice", "LibreOffice", "Office;"),
    ("exe:/usr/bin/kate", "Kate", "Utility;TextEditor;"),
    ("exe:/usr/bin/okular", "Okular", "Office;Viewer;"),
    ("exe:/usr/bin/gcompris-qt", "GCompris", "Education;Game;"),
    ("exe:/usr/bin/krita", "Krita", "Graphics;"),
    ("exe:/usr/bin/vlc", "VLC", "AudioVideo;Player;"),
    ("exe:/usr/bin/steam", "Steam", "Game;"),
    ("exe:/usr/bin/konsole", "Konsole", "System;TerminalEmulator;"),
    ("flatpak:net.supertuxkart.SuperTuxKart", "SuperTuxKart", "Game;"),
    ("flatpak:org.tuxpaint.Tuxpaint", "Tux Paint", "Education;Graphics;"),
]
WIN_APPS = [
    ("exe:C:\\Program Files\\Mozilla Firefox\\firefox.exe", "Firefox", ""),
    ("exe:C:\\Program Files\\LibreOffice\\program\\soffice.exe", "LibreOffice", ""),
    ("exe:C:\\Program Files\\Krita (x64)\\bin\\krita.exe", "Krita", ""),
    ("exe:C:\\Program Files (x86)\\Steam\\steam.exe", "Steam", ""),
    ("exe:C:\\Program Files\\VideoLAN\\VLC\\vlc.exe", "VLC", ""),
    ("exe:C:\\Windows\\System32\\cmd.exe", "Eingabeaufforderung (Shell)", ""),
    ("appx:Microsoft.WindowsCalculator_8wekyb3d8bbwe", "Rechner", ""),
    ("appx:Microsoft.Paint_8wekyb3d8bbwe", "Paint", ""),
]
GAMES = [("game:steam:9100010", "Sky Builders"), ("game:steam:9100020", "Kart Rally"), ("game:heroic:gog:91003", "Pixel Quest")]


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@dataclass
class Agent:
    """Simulierter Agent: hält die WebSocket-Verbindung (Gerät „online“) und beantwortet Pings."""
    base: str
    token: str
    hostname: str
    platform: str
    ws: object = None
    task: asyncio.Task | None = None
    inbox: list = field(default_factory=list)

    async def connect(self) -> None:
        url = self.base.replace("http", "ws", 1) + "/api/v1/ws"
        self.ws = await websockets.connect(url, additional_headers={"Authorization": f"Bearer {self.token}"})
        await self.send(type="hello", protocol_version=1, agent_version="0.32.7", hostname=self.hostname,
                        platform=self.platform)
        self.task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await self.send(type="pong")
                elif msg.get("type") == "policy":
                    await self.send(type="policy_ack", version=msg.get("version", ""))
                self.inbox.append(msg)
        except websockets.ConnectionClosed:
            pass

    async def send(self, **msg) -> None:
        await self.ws.send(json.dumps(msg, default=str))

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)


class Web:
    """Eltern-WebUI per HTTP (Formulare mit CSRF-Token)."""

    def __init__(self, base: str, lang: str):
        self.c = httpx.AsyncClient(base_url=base, headers={"Accept-Language": lang})

    async def csrf(self, path: str = "/children") -> str:
        return re.search(r'name="csrf_token" value="([^"]+)"', (await self.c.get(path)).text).group(1)

    async def login(self, user: str, secret: str) -> None:
        r = await self.c.post("/login", data={"csrf_token": await self.csrf("/login"), "username": user,
                                              "password": PASSWORD, "totp": pyotp.TOTP(secret).now()})
        if r.status_code >= 400 or (await self.c.get("/", follow_redirects=False)).status_code != 200:
            raise SystemExit(f"Login fehlgeschlagen ({r.status_code})")

    async def post(self, path: str, data: dict) -> httpx.Response:
        r = await self.c.post(path, data={"csrf_token": await self.csrf(), **data})
        if r.status_code >= 400:
            raise SystemExit(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r


async def pair(web: Web, name: str, hostname: str) -> tuple[int, str]:
    page = (await web.post("/devices/pair", {"name": name})).text
    code = re.search(r"enroll \S+ (\w{8})", page).group(1)
    r = await web.c.post("/api/v1/enroll", json={"code": code, "hostname": hostname})
    r.raise_for_status()
    return r.json()["device_id"], r.json()["token"]


class DB:
    """Lesezugriffe (IDs) und die älteren Nutzungszeiten – direkt per SQL auf die Demo-Datenbank."""

    def __init__(self, url: str):
        self.engine = create_engine(url)

    def execute(self, sql: str, params: dict | None = None) -> list:
        with self.engine.begin() as con:
            res = con.execute(text(sql), params or {})
            return res.fetchall() if res.returns_rows else []

    def one(self, sql: str, **params):
        return self.execute(sql, params)[0][0]

    def close(self) -> None:
        self.engine.dispose()


async def seed(base: str, database_url: str, secret: str, lang: str = "de") -> tuple[Web, list[Agent]]:
    """Legt die Demo-Daten an. Rückgabe: angemeldeter Web-Client (Cookies für Playwright) und verbundene Agenten."""
    t = T[lang]
    rnd = random.Random(42)
    db = DB(database_url)
    today = date.today()
    now = datetime.now().astimezone()

    web = Web(base, lang)
    await web.login(PARENT[lang], secret)

    # Kinder mit Wochenvorlage (Werktage/Wochenende), Spiele-Limit
    ids = {}
    for name in ("Lena", "Max"):
        await web.post("/children", {"name": name})
        # Namen sind je Familie verschlüsselt – die frisch angelegte Datenbank vergibt die IDs in Reihenfolge
        ids[name] = db.one("select max(id) from child")
    await web.post(f"/children/{ids['Lena']}/schedule", {
        "quota_wd": "120", "start_wd": "08:00", "end_wd": "20:30", "games_wd": "45",
        "quota_we": "180", "start_we": "09:00", "end_we": "21:00", "games_we": "90"})
    await web.post(f"/children/{ids['Max']}/schedule", {
        "quota_wd": "90", "start_wd": "08:00", "end_wd": "19:30", "games_wd": "30",
        "quota_we": "150", "start_we": "09:00", "end_we": "20:00", "games_we": "60"})

    # Rechner koppeln, Agenten verbinden, Benutzer und Programme melden
    agents = {}
    for key, name, host, platform, users, apps in (
            ("laptop", t["laptop"], "lena-laptop", "linux", [("lena", 1000, t["lena_full"])], LINUX_APPS),
            ("pc", t["pc"], "familien-pc", "windows",
             [("lena", 1001, t["lena_full"]), ("max", 1002, t["max_full"]), ("papa", 1003, "Papa")], WIN_APPS)):
        dev_id, token = await pair(web, name, host)
        a = Agent(base, token, host, platform)
        await a.connect()
        a.device_id = dev_id
        await a.send(type="report_users", users=[{"username": u, "uid": uid, "full_name": f} for u, uid, f in users])
        await a.send(type="report_apps", apps=[{"key": k, "name": n, "categories": [c for c in cats.split(";") if c]}
                                               for k, n, cats in apps])
        await a.send(type="integrity", checks=[
            {"id": "service", "label": "Agent-Dienst", "ok": True},
            {"id": "lock", "label": "Login-Sperre", "ok": True},
            {"id": "dns", "label": "Website-Filter", "ok": True},
            {"id": "exec", "label": "Ausführverbot", "ok": True}])
        agents[key] = a
    await asyncio.sleep(0.5)
    for key, mapping in (("laptop", {"lena": ids["Lena"]}), ("pc", {"lena": ids["Lena"], "max": ids["Max"]})):
        dev_id = agents[key].device_id
        # Benutzernamen sind verschlüsselt gespeichert – Zuordnung aus der Geräteseite lesen (wie das Formular)
        page = (await web.c.get(f"/devices/{dev_id}")).text
        rows = re.findall(r'<tr[^>]*>\s*<td>(\w+)[^<]*</td>\s*(?:<td>\d+</td>\s*)?<td>\s*<select name="(user_\d+)"', page)
        form = {field: str(mapping.get(u, "")) for u, field in rows}
        assert form, f"keine Benutzer auf Gerät {dev_id}"
        await web.post(f"/devices/{dev_id}/mapping", form)

    # Programme und Website-Filter
    await web.post(f"/children/{ids['Lena']}/programs", {
        "program_mode": "enforce",
        "app": [k for k, *_ in LINUX_APPS + WIN_APPS if not any(x in k for x in ("konsole", "cmd.exe", "steam"))]
               + ["exe:/usr/bin/steam"],
        "game": ["flatpak:net.supertuxkart.SuperTuxKart"]})
    await web.post(f"/children/{ids['Max']}/programs", {
        "program_mode": "audit", "app": [k for k, *_ in WIN_APPS if "cmd.exe" not in k]})
    await web.post(f"/children/{ids['Lena']}/web", {
        "web_mode": "whitelist", "safesearch": "1", "bundle": ["wikipedia", "youtube", "anton", "microsoft365"],
        "allow": "schule.example\nbibliothek.example", "deny": ""})
    await web.post(f"/children/{ids['Max']}/web", {
        "web_mode": "whitelist", "safesearch": "1", "bundle": ["wikipedia", "anton", "khanacademy"],
        "allow": "schule.example", "deny": "youtube.com"})

    # Nutzung: 14 Tage. Die letzten 7 Tage meldet der Agent, ältere schreibt das Skript direkt.
    plan = {"lena": [("laptop", 0.7), ("pc", 0.3)], "max": [("pc", 1.0)]}
    quota = {"lena": (120, 180), "max": (90, 150)}
    usage: dict[str, list] = {"laptop": [], "pc": []}
    app_usage: dict[str, list] = {"laptop": [], "pc": []}
    for back in range(13, -1, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 5
        for user, devs in plan.items():
            q = quota[user][1 if weekend else 0]
            minutes = q * rnd.uniform(0.55, 1.0) if back else q * (0.62 if user == "lena" else 0.8)
            if back in (3, 9) and user == "max":
                minutes = q + 15  # Verlängerung genehmigt
            games = minutes * rnd.uniform(0.2, 0.45)
            for dev, share in devs:
                secs, gsecs = int(minutes * share * 60), int(games * share * 60)
                if back > 6:
                    child_id = ids["Lena" if user == "lena" else "Max"]
                    db.execute("insert into usage_record (device_id, username, day, child_id, seconds, game_seconds,"
                               " updated_at, family_id) select :dev, :user, :day, :child, :s, :g, :at, family_id"
                               " from device where id = :dev",
                               {"dev": agents[dev].device_id, "user": user, "day": day, "child": child_id, "s": secs,
                                "g": gsecs, "at": datetime.now(timezone.utc).replace(tzinfo=None)})
                    continue
                usage[dev].append({"username": user, "day": day.isoformat(), "seconds": secs, "game_seconds": gsecs})
                apps = LINUX_APPS if dev == "laptop" else WIN_APPS
                rest = secs - gsecs
                for (k, *_), w in zip(apps[:4], (0.45, 0.25, 0.15, 0.15)):
                    app_usage[dev].append({"username": user, "day": day.isoformat(), "key": k, "seconds": int(rest * w)})
                for (k, n), w in zip(GAMES, (0.6, 0.3, 0.1)):
                    if user == "max" or k != GAMES[2][0]:
                        app_usage[dev].append({"username": user, "day": day.isoformat(), "key": k, "name": n,
                                               "seconds": int(gsecs * w)})
    for dev, a in agents.items():
        await a.send(type="usage_report", entries=usage[dev])
        await a.send(type="app_usage_report", entries=app_usage[dev],
                     playing=[{"username": "max", "key": GAMES[1][0], "name": GAMES[1][1]}] if dev == "pc" else [])

    # Gesperrte Aktionen (heute und gestern)
    def at(hours: float) -> str:
        return utc_iso(now - timedelta(hours=hours))
    await agents["laptop"].send(type="blocked", events=[
        {"username": "lena", "at": at(0.4), "kind": "domain", "key": "domain:games.example", "name": "games.example",
         "detail_code": "whitelist"},
        {"username": "lena", "at": at(1.2), "kind": "program", "key": "exe:/usr/bin/konsole", "name": "Konsole"},
        {"username": "lena", "at": at(2.5), "kind": "domain", "key": "domain:chat.example", "name": "chat.example",
         "detail_code": "whitelist"},
        {"username": "lena", "at": at(26), "kind": "program", "key": "exe:/home/lena/Downloads/tool",
         "name": "tool", "detail_code": "home"}])
    await agents["pc"].send(type="blocked", events=[
        {"username": "max", "at": at(0.8), "kind": "domain", "key": "domain:youtube.com", "name": "youtube.com",
         "detail_code": "deny"},
        {"username": "max", "at": at(1.5), "kind": "program", "key": WIN_APPS[5][0], "name": WIN_APPS[5][1],
         "audit": True},
        {"username": "max", "at": at(3), "kind": "domain", "key": "domain:scratch.mit.edu", "name": "scratch.mit.edu",
         "detail_code": "whitelist"},
        {"username": "max", "at": at(27), "kind": "domain", "key": "domain:video.example", "name": "video.example",
         "detail_code": "whitelist"}])

    # Chat und Anfragen
    mid = lambda: uuid.uuid4().hex  # noqa: E731
    lap, pc = agents["laptop"], agents["pc"]
    await lap.send(type="chat_send", client_msg_id=mid(), username="lena", body=t["chat_lena"])
    await asyncio.sleep(0.3)
    lena_chat = ids["Lena"]
    await web.post(f"/children/{lena_chat}/chat", {"body": t["chat_reply"]})
    await lap.send(type="extension_request", client_msg_id=mid(), username="lena", day=today.isoformat(),
                   minutes=30, reason=t["ext_reason"])
    await pc.send(type="chat_send", client_msg_id=mid(), username="max", body=t["chat_max"])
    await pc.send(type="access_request", client_msg_id=mid(), username="max", access_kind="domain",
                  key="domain:scratch.mit.edu", name="scratch.mit.edu", reason="")
    await pc.send(type="extension_request", client_msg_id=mid(), username="max", day=today.isoformat(),
                  minutes=20, reason=t["games_reason"], target="games")
    await lap.send(type="access_request", client_msg_id=mid(), username="lena", access_kind="program",
                   key="exe:/usr/bin/krita", name="Krita", reason=t["access_reason"])
    await asyncio.sleep(0.8)
    # Lenas Verlängerung genehmigen (mit Antwort), Krita-Anfrage offen lassen, Max' Anfragen offen lassen
    ext = db.one("select id from message where child_id = :c and kind = 'extension_request' order by id desc",
                 c=ids["Lena"])
    await web.post(f"/children/{ids['Lena']}/requests/{ext}", {"action": "approve", "minutes": "30"})
    await web.post(f"/children/{ids['Lena']}/chat", {"body": t["approve_reply"]})
    await asyncio.sleep(0.3)
    db.close()
    web.ids = ids
    return web, list(agents.values())


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--database-url", required=True, help="Datenbank des Demo-Servers (nie die Entwicklungs-DB)")
    ap.add_argument("--secret", required=True, help="TOTP-Secret des Demo-Eltern-Accounts")
    ap.add_argument("--lang", choices=("de", "en"), default="de")
    a = ap.parse_args()
    if a.database_url.rstrip("/").endswith("/kidctl"):
        raise SystemExit("Nicht in die Entwicklungsdatenbank schreiben – eigene Datenbank nutzen.")
    web, agents = await seed(a.url, a.database_url, a.secret, a.lang)
    print("Demo-Daten angelegt, Agenten verbunden. Strg+C beendet.")
    try:
        await asyncio.Event().wait()
    finally:
        for ag in agents:
            await ag.close()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
