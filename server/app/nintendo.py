"""Nintendo-Switch-Spielzeit (experimentell, Phase 1: nur lesen).

- Inoffizielle Cloud-API der App „Nintendo Switch Parental Controls“ über ``pynintendoparental`` (wie die
  Home-Assistant-Integration ``nintendo_parental_controls``). Kann jederzeit brechen – in UI und Doku „experimentell“.
- **Einrichtung:** ``start_link`` erzeugt den Login-Link und merkt sich den PKCE-Verifier verschlüsselt (zwei Anfragen,
  auch über einen Neustart). Die Eltern kopieren den Link hinter „Diese Person auswählen“ (``npf…://auth#…``),
  ``complete_link`` tauscht ihn gegen das Session-Token. Das Token ist ein Vollzugriff auf das Nintendo-Konto:
  verschlüsselt mit dem Familienschlüssel, nie im Export, nie im Log.
- **Abruf:** ``run_forever`` fragt je verknüpfter Familie alle ``KIDCTL_NINTENDO_INTERVAL`` Sekunden ab. Nutzung,
  Limit und Restzeit gelten je Konsole; die Spielzeit je Spielerprofil landet in ``nintendo_usage`` beim zugeordneten
  Kind. Sie wird nur angezeigt und zählt nicht ins Kontingent.
- **Fehler:** Token ungültig → Status „reauth“, kein weiterer Abruf bis zur neuen Anmeldung. Netz-/API-Fehler →
  letzte Werte bleiben, Konsolen „stale“, nächster Versuch mit Backoff (bis 15 min wie in HA).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import schedule as sch
from .config import settings
from .models import Child, NintendoConsole, NintendoLink, NintendoPlayer, NintendoUsage, utcnow

log = logging.getLogger("kidctl.nintendo")
# Die Bibliothek schreibt auf DEBUG ganze Tagesübersichten (Spiele, Nicknames) – nie mitschreiben
for _name in ("pynintendoparental", "pynintendoauth"):
    logging.getLogger(_name).setLevel(logging.WARNING)

LINK_PREFIX = "npf54789befb391a838://auth"
LINK_MAX = 4096
PENDING_MINUTES = 30        # so lange gilt ein erzeugter Login-Link
MAX_BACKOFF = 900           # Sekunden, wie retry_after in HA
TIMEOUT = 30                # Sekunden je Abruf
TICK = 15                   # Sekunden zwischen Prüfungen, welche Familie dran ist
API_LANG = "de-DE"

OK, ERROR, REAUTH = "ok", "error", "reauth"


class LinkError(ValueError):
    """Eingefügter Link passt nicht oder Nintendo hat ihn abgelehnt (Text für die WebUI)."""


@dataclass
class PlayerData:
    key: str
    nickname: str = ""
    minutes: int | None = None
    apps: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class ConsoleData:
    key: str
    name: str = ""
    model: str = ""
    used_minutes: int | None = None
    limit_minutes: int | None = None
    remaining_minutes: int | None = None
    players: list[PlayerData] = field(default_factory=list)


# ---------- Abbildung der Bibliothek auf kidCTL (rein, testbar) ----------

def _int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def snapshot(dev) -> ConsoleData:
    """Werte eines ``pynintendoparental.Device`` – fehlende Felder (neue Konsole) werden toleriert."""
    limit = _int(getattr(dev, "limit_time", None))
    unlimited = limit is None or limit < 0 or getattr(dev, "extra_playing_time", None) == -1
    has_today = bool(getattr(dev, "daily_summaries", None))
    players = []
    for p in getattr(dev, "players", None) or []:
        key = getattr(p, "player_id", None)
        if not key:
            continue
        apps = []
        for a in getattr(p, "apps", None) or []:
            name = getattr(getattr(a, "application", None), "name", None)
            minutes = _int(getattr(a, "playing_time", None))
            if name and minutes:
                apps.append((str(name)[:128], minutes))
        players.append(PlayerData(key=str(key), nickname=str(getattr(p, "nickname", "") or "")[:64],
                                  minutes=_int(getattr(p, "playing_time", None)) if has_today else None,
                                  apps=sorted(apps, key=lambda x: -x[1])))
    return ConsoleData(
        key=str(dev.device_id), name=str(getattr(dev, "name", "") or "")[:64],
        model=str(getattr(dev, "model", "") or "")[:16],
        used_minutes=_int(getattr(dev, "today_playing_time", None)) if has_today else None,
        limit_minutes=None if unlimited else limit,
        remaining_minutes=None if unlimited else _int(getattr(dev, "today_time_remaining", None)),
        players=players)


# ---------- Speichern ----------

def store(db: Session, consoles: list[ConsoleData], failed: set[str], day: date) -> int:
    """Abgerufene Konsolen übernehmen; ``failed`` = Konsolen, deren Abruf scheiterte (letzter Wert bleibt, stale).
    Rückgabe: Zahl neu gesehener Spielerprofile ohne Zuordnung (nur fürs Log)."""
    now, new_unmapped = utcnow(), 0
    known = {c.device_key: c for c in db.scalars(select(NintendoConsole))}
    for data in consoles:
        con = known.get(data.key)
        if con is None:
            con = NintendoConsole(device_key=data.key)
            db.add(con)
        con.name, con.model = data.name or con.name, data.model or con.model
        if data.key in failed:
            con.stale = True
            continue
        # „Stand“ = wann zuletzt neue Spielzeit kam. Das last_sync der Bibliothek taugt nicht: Es ist der Zeitpunkt,
        # an dem die Switch zuletzt die Kindersicherungs-Einstellungen übernommen hat, nicht die Spielzeit.
        if data.used_minutes is not None and (con.day != day or con.used_minutes != data.used_minutes):
            con.last_sync = now
        con.day, con.used_minutes = day, data.used_minutes
        con.limit_minutes, con.remaining_minutes = data.limit_minutes, data.remaining_minutes
        con.stale, con.updated_at = False, now
        db.flush()
        players = {p.player_key: p for p in con.players}
        for pd in data.players:
            pl = players.get(pd.key)
            if pl is None:
                pl = NintendoPlayer(console_id=con.id, player_key=pd.key)
                con.players.append(pl)
                new_unmapped += 1
            pl.nickname = pd.nickname or pl.nickname
            db.flush()
            if pd.minutes is None:
                continue
            rec = db.scalar(select(NintendoUsage).where(NintendoUsage.player_id == pl.id, NintendoUsage.day == day))
            if rec is None:
                rec = NintendoUsage(player_id=pl.id, day=day)
                db.add(rec)
            rec.child_id, rec.minutes = pl.child_id, pd.minutes
            rec.apps, rec.updated_at = json.dumps(pd.apps, ensure_ascii=False), now
    for key, con in known.items():
        if key in failed:
            con.stale = True
    return new_unmapped


def mark_stale(db: Session) -> None:
    for con in db.scalars(select(NintendoConsole)):
        con.stale = True


def assign(db: Session, player_id: int, child_id: int | None) -> None:
    """Spielerprofil einem Kind zuordnen (``None`` = keinem); die Spielzeit von heute zieht mit um."""
    pl = db.get(NintendoPlayer, player_id)
    if pl is None:
        raise LookupError(player_id)
    if child_id is not None and db.get(Child, child_id) is None:
        raise LookupError(child_id)
    pl.child_id = child_id
    for rec in db.scalars(select(NintendoUsage).where(NintendoUsage.player_id == pl.id,
                                                      NintendoUsage.day == sch.today_local())):
        rec.child_id = child_id


def unlink(db: Session) -> None:
    """Verknüpfung trennen: Token, Konsolen und Profile weg; die Spielzeit je Kind bleibt für die Statistik."""
    for model in (NintendoConsole, NintendoLink):
        for row in db.scalars(select(model)):
            db.delete(row)
    _clients.pop(_family(db), None)


def _family(db: Session) -> int | None:
    from .tenant import family_of
    return family_of(db)


# ---------- Einrichtung ----------

def link(db: Session) -> NintendoLink | None:
    return db.scalar(select(NintendoLink))


def pending_url(row: NintendoLink | None) -> str | None:
    if row and row.pending_url and row.pending_at and utcnow() - row.pending_at < timedelta(minutes=PENDING_MINUTES):
        return row.pending_url
    return None


async def start_link(db: Session) -> str:
    """Neuen Login-Link erzeugen (lokal, ohne Netz) und Verifier merken."""
    import aiohttp
    from pynintendoparental import Authenticator
    async with aiohttp.ClientSession() as session:
        auth = Authenticator(client_session=session)
    row = link(db) or NintendoLink()
    db.add(row)
    # Der Verifier ist in der Bibliothek nur ein Attribut des Objekts; wir brauchen ihn in der nächsten Anfrage
    row.pending_verifier, row.pending_url, row.pending_at = auth._auth_code_verifier, auth.login_url, utcnow()
    return auth.login_url


async def complete_link(db: Session, pasted: str) -> None:
    """Kopierten Link gegen das Session-Token tauschen und speichern; Abruf startet sofort."""
    import aiohttp
    from pynintendoauth.exceptions import HttpException
    from pynintendoparental import Authenticator
    pasted = (pasted or "").strip()
    row = link(db)
    if row is None or pending_url(row) is None or not row.pending_verifier:
        raise LinkError("expired")
    if len(pasted) > LINK_MAX or not pasted.startswith(LINK_PREFIX) or "session_token_code=" not in pasted:
        raise LinkError("format")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            auth = Authenticator(client_session=session)
            auth._auth_code_verifier = row.pending_verifier
            await auth.async_complete_login(pasted)
    except (ValueError, KeyError, HttpException) as e:
        log.info("Nintendo: Anmeldung abgelehnt (%s)", type(e).__name__)
        raise LinkError("rejected") from e
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Nintendo: Anmeldung nicht erreichbar (%s)", type(e).__name__)
        raise LinkError("network") from e
    if not auth.session_token:
        raise LinkError("rejected")
    row.session_token = auth.session_token
    row.pending_verifier = row.pending_url = row.pending_at = None
    row.status, row.error, row.failures, row.next_poll_at = OK, "", 0, utcnow()
    _clients.pop(_family(db), None)


# ---------- Abruf ----------

# Je Familie ein angemeldeter Client (Access-Token wird nur bei Ablauf erneuert, wie in HA)
_clients: dict[int, tuple[str, object]] = {}


async def _fetch(family_id: int, token: str, session) -> tuple[list[ConsoleData], set[str]]:
    from pynintendoparental import Authenticator, NintendoParental
    cached = _clients.get(family_id)
    if cached is None or cached[0] != token:
        auth = Authenticator(session_token=token, client_session=session)
        await auth.async_complete_login(use_session_token=True)
        cached = _clients[family_id] = (token, NintendoParental(auth, settings.timezone, API_LANG))
    api = cached[1]
    devices = await api.discover_devices()
    failed: set[str] = set()
    for dev in devices.values():
        try:
            await dev.update(monthly_summary=False)
        except _auth_errors():
            raise
        except Exception as e:   # eine Konsole mit Fehler darf die anderen nicht aufhalten
            log.warning("Nintendo: Konsole nicht abrufbar (%s)", type(e).__name__)
            failed.add(dev.device_id)
    return [snapshot(d) for d in devices.values()], failed


def _auth_errors() -> tuple[type, ...]:
    from pynintendoauth.exceptions import InvalidOAuthConfigurationException, InvalidSessionTokenException
    return InvalidSessionTokenException, InvalidOAuthConfigurationException


def _error_code(e: Exception) -> str:
    from pynintendoauth.exceptions import HttpException
    from pynintendoparental.exceptions import NoDevicesFoundException
    if isinstance(e, NoDevicesFoundException):
        return "no_devices"
    if isinstance(e, HttpException):
        return "update_required" if e.error_code == "update_required" else f"http_{e.status_code}"[:32]
    return "network"


def backoff(failures: int) -> int:
    return min(settings.nintendo_interval * 2 ** max(failures - 1, 0), MAX_BACKOFF)


async def poll_family(family_id: int, session) -> str:
    """Eine Familie abrufen und speichern; Rückgabe: neuer Status."""
    from .tenant import family_session
    with family_session(family_id) as db:
        row = link(db)
        token = row.session_token if row else None
    if not token:
        return ""
    try:
        consoles, failed = await _fetch(family_id, token, session)
        error = None
    except _auth_errors():
        _clients.pop(family_id, None)
        error = REAUTH
    except Exception as e:
        _clients.pop(family_id, None)
        error = _error_code(e)
        log.warning("Nintendo: Abruf für Familie %d fehlgeschlagen (%s, %s)", family_id, error, type(e).__name__)
    with family_session(family_id) as db:
        row = link(db)
        if row is None or row.session_token != token:   # inzwischen getrennt oder neu angemeldet
            return ""
        now = utcnow()
        if error == REAUTH:
            log.info("Nintendo: Familie %d muss sich neu anmelden", family_id)
            row.status, row.error, row.next_poll_at = REAUTH, "", None
            mark_stale(db)
        elif error:
            row.failures += 1
            row.status, row.error = ERROR, error
            row.next_poll_at = now + timedelta(seconds=backoff(row.failures))
            mark_stale(db)
        else:
            new = store(db, consoles, failed, sch.today_local())
            if new:
                log.info("Nintendo: Familie %d – %d neue Spielerprofile ohne Zuordnung", family_id, new)
            row.status, row.error, row.failures, row.last_ok_at = OK, "", 0, now
            row.next_poll_at = now + timedelta(seconds=settings.nintendo_interval)
        db.commit()
        return row.status


def due_families() -> list[int]:
    from .tenant import platform_session
    with platform_session() as db:
        return list(db.scalars(select(NintendoLink.family_id).where(
            NintendoLink.session_token.is_not(None), NintendoLink.status != REAUTH,
            NintendoLink.next_poll_at <= utcnow()).order_by(NintendoLink.next_poll_at)))


async def run_forever() -> None:
    if not settings.nintendo:
        return
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
        while True:
            try:
                for fid in due_families():
                    await poll_family(fid, session)
            except Exception:
                log.exception("Nintendo-Abruf fehlgeschlagen")
            await asyncio.sleep(TICK)


# ---------- Anzeige ----------

@dataclass
class ChildCard:
    console: NintendoConsole
    player: NintendoPlayer
    minutes: int | None
    apps: list


def for_child(db: Session, child_id: int) -> list[ChildCard]:
    """Heute-Tab: zugeordnete Profile mit dem Stand ihrer Konsole."""
    today = sch.today_local()
    cards = []
    for pl in db.scalars(select(NintendoPlayer).where(NintendoPlayer.child_id == child_id).order_by(NintendoPlayer.id)):
        rec = db.scalar(select(NintendoUsage).where(NintendoUsage.player_id == pl.id, NintendoUsage.day == today))
        cards.append(ChildCard(console=pl.console, player=pl, minutes=rec.minutes if rec else None,
                               apps=json.loads(rec.apps) if rec and rec.apps else []))
    return cards


def daily(db: Session, child_id: int, days: int) -> list[tuple[date, int]]:
    """Statistik: Switch-Minuten des Kindes je Tag (alle Profile), älteste zuerst."""
    from sqlalchemy import func
    today = sch.today_local()
    start = today - timedelta(days=days - 1)
    used = dict(db.execute(select(NintendoUsage.day, func.sum(NintendoUsage.minutes)).where(
        NintendoUsage.child_id == child_id, NintendoUsage.day >= start).group_by(NintendoUsage.day)).all())
    return [(start + timedelta(days=i), int(used.get(start + timedelta(days=i), 0))) for i in range(days)]


def has_data(db: Session, child_id: int) -> bool:
    return db.scalar(select(NintendoPlayer.id).where(NintendoPlayer.child_id == child_id).limit(1)) is not None \
        or db.scalar(select(NintendoUsage.id).where(NintendoUsage.child_id == child_id).limit(1)) is not None
