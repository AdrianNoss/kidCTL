"""Website-Filter: Dienst-Bundles, Sperrlisten (Download/Verteilung), Einstellungen je Kind."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from .config import settings
from .db import SessionLocal
from .i18n import N_
from .models import BlocklistState, Child, ChildBlocklist, ChildDomain, ChildWebBundle, utcnow

log = logging.getLogger("kidctl.webfilter")

MODES = {"off": N_("Aus"), "whitelist": N_("Whitelist (nur Freigegebenes)"), "blacklist": N_("Blacklist (alles außer Gesperrtem)")}


# ---------- Dienst-Bundles (Whitelist) ----------
# Domains inkl. Subdomains. CNAME-Ziele löst der Resolver mit auf (z. B. Wikipedia -> wikimedia).

@dataclass(frozen=True)
class Bundle:
    id: str
    name: str
    domains: tuple[str, ...]
    note: str = ""


BUNDLES = {b.id: b for b in [
    Bundle("youtube", "YouTube", (
        "youtube.com", "youtu.be", "youtube-nocookie.com", "youtubekids.com", "googlevideo.com",
        "ytimg.com", "ggpht.com", "youtubei.googleapis.com", "youtube.googleapis.com",
        "yt3.googleusercontent.com", "gstatic.com", "fonts.googleapis.com", "jnn-pa.googleapis.com")),
    Bundle("wikipedia", "Wikipedia", (
        "wikipedia.org", "wikimedia.org", "wikidata.org", "wikibooks.org", "wiktionary.org",
        "wikiversity.org", "wikisource.org", "wikiquote.org", "wikinews.org", "mediawiki.org",
        "wmfusercontent.org")),
    Bundle("anton", N_("ANTON (Lern-App)"), ("anton.app",)),
    Bundle("scratch", "Scratch", ("scratch.mit.edu", "scratchfoundation.org")),
    Bundle("khanacademy", "Khan Academy", ("khanacademy.org", "kastatic.org", "kasandbox.org"),
           note=N_("Videos laufen über YouTube – ggf. YouTube-Bundle zusätzlich wählen.")),
    Bundle("microsoft365", N_("Microsoft 365 / Teams (Schule)"), (
        "office.com", "office.net", "office365.com", "microsoft365.com", "microsoftonline.com",
        "login.microsoft.com", "teams.microsoft.com", "teams.cdn.office.net", "live.com", "sharepoint.com",
        "onedrive.com", "msauth.net", "msftauth.net", "msauthimages.net", "msocdn.com", "lync.com",
        "skype.com", "aka.ms", "static.microsoft")),
]}


# ---------- Sperrlisten (Blacklist) ----------

@dataclass(frozen=True)
class Blocklist:
    id: str
    name: str
    url: str
    description: str = ""
    report: bool = True  # Treffer ins Sperrprotokoll (Werbe-/Tracker-Listen: nein, zu viele)


BLOCKLISTS = {b.id: b for b in [
    Blocklist("hagezi-multi", "HaGeZi Multi Normal",
              "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/multi-onlydomains.txt",
              N_("Werbung, Tracking, Malware"), report=False),
    Blocklist("hagezi-pro", "HaGeZi Multi Pro",
              "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/pro-onlydomains.txt",
              N_("wie Normal, strenger"), report=False),
    Blocklist("hagezi-nsfw", "HaGeZi NSFW",
              "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/nsfw-onlydomains.txt",
              N_("Erwachseneninhalte")),
    Blocklist("hagezi-gambling", N_("HaGeZi Glücksspiel"),
              "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/gambling-onlydomains.txt",
              N_("Glücksspiel")),
    Blocklist("oisd-big", "OISD big", "https://big.oisd.nl/domainswild2", N_("Werbung, Tracking, Malware"),
              report=False),
    Blocklist("oisd-nsfw", "OISD NSFW", "https://nsfw.oisd.nl/domainswild2", N_("Erwachseneninhalte")),
    Blocklist("stevenblack", "StevenBlack Unified",
              "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts", N_("Werbung, Malware"),
              report=False),
    Blocklist("stevenblack-porn", "StevenBlack Porn",
              "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts",
              N_("Erwachseneninhalte")),
    Blocklist("stevenblack-gambling", N_("StevenBlack Glücksspiel"),
              "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/gambling-only/hosts",
              N_("Glücksspiel")),
]}
DEFAULT_LISTS = ("hagezi-multi", "hagezi-nsfw")  # Vorauswahl im Blacklist-Modus
REFRESH_SECONDS = 24 * 3600
MAX_LIST_BYTES = 64 * 1024 * 1024


# ---------- Domains ----------

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_domain(raw: str) -> str | None:
    """„https://Www.Example.com/x“ -> „www.example.com“; ungültig -> None."""
    d = raw.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split(":")[0].strip(".")
    d = d.removeprefix("*.")
    if not d:
        return None
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = d.split(".")
    if len(d) > 253 or len(labels) < 2 or not all(_LABEL.match(x) for x in labels):
        return None
    return d


def parse_domains(text: str) -> list[str]:
    """Mehrere Formate: reine Domains, hosts (``0.0.0.0 domain``), Adblock (``||domain^``)."""
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("!"):
            continue
        if line.startswith("||"):
            line = line[2:].split("^", 1)[0]
        else:
            parts = line.split()
            line = parts[1] if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1", "::") else parts[0]
        d = normalize_domain(line)
        if d and d not in ("localhost", "localhost.localdomain"):
            out.add(d)
    return sorted(out)


def parse_domain_input(text: str) -> tuple[list[str], list[str]]:
    """Eingabe aus dem WebUI (Zeilen/Kommas) -> (gültige Domains, ungültige Eingaben)."""
    ok, bad = [], []
    for item in re.split(r"[\s,;]+", text):
        if not item:
            continue
        d = normalize_domain(item)
        (ok if d else bad).append(d or item)
    return sorted(set(ok)), bad


# ---------- Listen herunterladen / ausliefern ----------

def lists_dir() -> Path:
    d = Path(settings.data_dir) / "lists"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_file(list_id: str) -> Path:
    return lists_dir() / f"{list_id}.txt"


def lists_in_use(db: Session) -> set[str]:
    return set(db.scalars(select(ChildBlocklist.list_id).join(Child, Child.id == ChildBlocklist.child_id)
                          .where(Child.web_mode == "blacklist")))


async def fetch_list(list_id: str, client: httpx.AsyncClient) -> bool:
    """Lädt eine Liste, normalisiert sie und speichert sie bei Änderung. True = neue Version."""
    bl = BLOCKLISTS[list_id]
    with SessionLocal() as db:
        state = db.get(BlocklistState, list_id) or BlocklistState(id=list_id)
        db.add(state)
        try:
            r = await client.get(bl.url, timeout=60, follow_redirects=True)
            r.raise_for_status()
            if len(r.content) > MAX_LIST_BYTES:
                raise ValueError("Liste zu groß")
            domains = await asyncio.to_thread(parse_domains, r.text)
            if not domains:
                raise ValueError("Liste leer")
        except (httpx.HTTPError, ValueError) as e:
            state.error = str(e)[:255]
            db.commit()
            log.warning("Sperrliste %s nicht geladen: %s", list_id, e)
            return False
        body = ("\n".join(domains) + "\n").encode()
        sha = hashlib.sha256(body).hexdigest()
        changed = sha != state.sha256
        if changed:
            tmp = list_file(list_id).with_suffix(".tmp")
            tmp.write_bytes(body)
            os.replace(tmp, list_file(list_id))
        state.sha256, state.count, state.fetched_at, state.error = sha, len(domains), utcnow(), ""
        db.commit()
        log.info("Sperrliste %s: %d Domains%s", list_id, len(domains), " (neu)" if changed else "")
        return changed


async def refresh_lists(only: set[str] | None = None) -> set[str]:
    """Benutzte (oder angegebene) Listen aktualisieren. Gibt geänderte IDs zurück."""
    with SessionLocal() as db:
        ids = (only if only is not None else lists_in_use(db)) & BLOCKLISTS.keys()
    changed = set()
    async with httpx.AsyncClient(headers={"User-Agent": "kidctl"}) as client:
        for list_id in sorted(ids):
            if await fetch_list(list_id, client):
                changed.add(list_id)
    return changed


# ---------- Einstellungen je Kind ----------

def child_settings(db: Session, child_id: int) -> dict:
    bundles = set(db.scalars(select(ChildWebBundle.bundle).where(ChildWebBundle.child_id == child_id)))
    domains = db.execute(select(ChildDomain.domain, ChildDomain.action)
                         .where(ChildDomain.child_id == child_id).order_by(ChildDomain.domain)).all()
    lists = set(db.scalars(select(ChildBlocklist.list_id).where(ChildBlocklist.child_id == child_id)))
    return {"bundles": bundles, "allow": [d for d, a in domains if a == "allow"],
            "deny": [d for d, a in domains if a == "deny"], "lists": lists}


def save_child_settings(db: Session, child: Child, mode: str, safesearch: bool, bundles: set[str],
                        allow: list[str], deny: list[str], lists: set[str]) -> None:
    child.web_mode, child.safesearch = mode, safesearch
    db.execute(delete(ChildWebBundle).where(ChildWebBundle.child_id == child.id))
    db.execute(delete(ChildDomain).where(ChildDomain.child_id == child.id))
    db.execute(delete(ChildBlocklist).where(ChildBlocklist.child_id == child.id))
    db.add_all([ChildWebBundle(child_id=child.id, bundle=b) for b in sorted(bundles & BUNDLES.keys())])
    db.add_all([ChildDomain(child_id=child.id, domain=d, action="allow") for d in allow])
    db.add_all([ChildDomain(child_id=child.id, domain=d, action="deny") for d in deny])
    db.add_all([ChildBlocklist(child_id=child.id, list_id=x) for x in sorted(lists & BLOCKLISTS.keys())])


def web_policy(db: Session, child: Child) -> p.WebFilter:
    s = child_settings(db, child.id)
    if child.web_mode == "off":
        return p.WebFilter(mode="off")
    allow = set(s["allow"])
    if child.web_mode == "whitelist":
        for b in s["bundles"]:
            if b in BUNDLES:
                allow.update(BUNDLES[b].domains)
    refs = []
    if child.web_mode == "blacklist":
        for list_id in sorted(s["lists"]):
            state = db.get(BlocklistState, list_id)
            bl = BLOCKLISTS.get(list_id)
            refs.append(p.BlocklistRef(id=list_id, sha256=state.sha256 if state else "",
                                       name=bl.name if bl else list_id, report=bl.report if bl else True))
    return p.WebFilter(mode=child.web_mode, allow=sorted(allow),
                       deny=s["deny"] if child.web_mode == "blacklist" else [],
                       lists=refs, safesearch=child.safesearch)
