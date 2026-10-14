#!/usr/bin/env python3
"""WebUI des Entwicklungsservers per HTTP bedienen (Tests mit der VM). Meldet sich als „mama“
an (TOTP aus ~/.local/share/kidctl-dev/secret.txt) und merkt sich die Session.

    dev/webctl.py child NAME                       -> Kind anlegen, gibt ID aus
    dev/webctl.py schedule CID QUOTA START END QUOTA_WE
    dev/webctl.py credit CID TAG MINUTEN
    dev/webctl.py exc-delete CID
    dev/webctl.py pair NAME                        -> Kopplungscode
    dev/webctl.py map GERAET_ID USER CID
    dev/webctl.py device GERAET_ID
    dev/webctl.py programs CID MODUS [SCHLÜSSEL …]
    dev/webctl.py blocked CID
    dev/webctl.py web CID MODUS [bundles=a,b] [allow=x.de,y.de] [deny=…] [lists=a,b] [safesearch=0|1]
    dev/webctl.py dash
"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import httpx
import pyotp

D = Path(os.environ.get("KIDCTL_DEV_DIR", Path.home() / ".local/share/kidctl-dev"))
B = "http://127.0.0.1:8000"
JAR = D / "cookies.json"


def tok(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def client() -> httpx.Client:
    c = httpx.Client(base_url=B, cookies=json.loads(JAR.read_text()) if JAR.exists() else {})
    if c.get("/", follow_redirects=False).status_code != 200:
        con = sqlite3.connect(D / "data" / "kidctl.db")
        con.execute("update parent set totp_last_step=0")  # Replay-Schutz für wiederholte Test-Logins
        con.commit()
        secret = (D / "secret.txt").read_text().split()[-1]
        c.post("/login", data={"csrf_token": tok(c.get("/login").text), "username": "mama",
                               "password": "ein-langes-passwort", "totp": pyotp.TOTP(secret).now()})
        JAR.write_text(json.dumps(dict(c.cookies)))
    return c


def main(cmd: str, *args: str) -> None:
    c = client()
    csrf = tok(c.get("/children").text)
    if cmd == "child":
        c.post("/children", data={"csrf_token": csrf, "name": args[0]})
        print(re.search(rf'href="/children/(\d+)">{re.escape(args[0])}<', c.get("/children").text).group(1))
    elif cmd == "schedule":
        cid, q, st, en, qwe = args
        print(c.post(f"/children/{cid}/schedule", data={
            "csrf_token": csrf, "quota_wd": q, "start_wd": st, "end_wd": en,
            "quota_we": qwe, "start_we": st, "end_we": en}).status_code)
    elif cmd == "credit":
        print(c.post(f"/children/{args[0]}/credit", data={
            "csrf_token": csrf, "day": args[1], "extra_minutes": args[2], "window_end": ""}).status_code)
    elif cmd == "exc-delete":
        page = c.get(f"/children/{args[0]}").text
        for eid in re.findall(rf"/children/{args[0]}/exceptions/(\d+)/delete", page):
            print(c.post(f"/children/{args[0]}/exceptions/{eid}/delete", data={"csrf_token": csrf}).status_code)
    elif cmd == "pair":
        r = c.post("/devices/pair", data={"csrf_token": csrf, "name": args[0]})
        print(re.search(r"enroll \S+ (\w{8})", r.text).group(1))
    elif cmd == "map":
        page = c.get(f"/devices/{args[0]}").text
        rows = re.findall(r'<td>(\w+)(?: \([^)]*\))?</td>\s*<td>\d+</td>\s*<td>\s*<select name="(user_\d+)"', page)
        print(c.post(f"/devices/{args[0]}/mapping", data={"csrf_token": csrf, dict(rows)[args[1]]: args[2]}).status_code)
    elif cmd == "device":
        page = c.get(f"/devices/{args[0]}").text
        print(" ".join(re.sub(r"<[^>]+>", " ", page.split("<h2>Benutzer-Zuordnung")[0].split("<dl>")[1]).split()))
    elif cmd == "programs":
        print(c.post(f"/children/{args[0]}/programs", data={
            "csrf_token": csrf, "program_mode": args[1], "app": list(args[2:])}).status_code)
    elif cmd == "web":
        opts = dict(a.split("=", 1) for a in args[2:])
        split = lambda k: [x for x in opts.get(k, "").split(",") if x]  # noqa: E731
        data = {"csrf_token": csrf, "web_mode": args[1], "bundle": split("bundles"), "list": split("lists"),
                "allow": "\n".join(split("allow")), "deny": "\n".join(split("deny"))}
        if opts.get("safesearch", "1") == "1":
            data["safesearch"] = "1"
        print(c.post(f"/children/{args[0]}/web", data=data).status_code)
    elif cmd == "blocked":
        page = c.get(f"/children/{args[0]}").text.split("Gesperrte Aktionen")[1]
        for row in re.findall(r"<tr>\s*<td>[^<]*</td>\s*<td>(.*?)</td>", page, re.S):
            print(" ".join(re.sub(r"<[^>]+>", " ", row).split()))
    elif cmd == "dash":
        print(" ".join(re.sub(r"<[^>]+>", " ", c.get("/").text.split("<h2>Kinder</h2>")[1].split("</table>")[0]).split()))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["help"]))
