"""Agent-API: Geräte-Kopplung (HTTP) und WebSocket-Endpunkt."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from kidctl_common import protocol as p

from .. import access, chat, integrity, nowplaying, policy, programs, selfupdate, stats, webfilter
from ..config import settings
from ..db import get_platform_db
from ..tenant import family_session, platform_session, set_family
from ..models import BlocklistState, Device, DeviceUser, PairingCode, utcnow
from ..security import (
    client_ip, is_rate_limited, new_token, normalize_pairing_code, record_failure, token_hash,
)
from ..ws import manager

log = logging.getLogger("kidctl.api")
router = APIRouter()

HELLO_TIMEOUT = 10  # Sekunden
LAST_SEEN_INTERVAL = timedelta(seconds=60)


@router.get(p.LISTS_PATH + "/{list_id}")
def blocklist_download(list_id: str, request: Request, db: Session = Depends(get_platform_db)):
    """Sperrliste für Agenten (Geräte-Token). ETag = SHA-256, damit unveränderte Listen nicht
    erneut übertragen werden."""
    auth = request.headers.get("authorization", "")
    dev = None
    if auth.lower().startswith("bearer "):
        dev = db.execute(select(Device.id, Device.revoked_at)   # Plattform: nur unverschlüsselte Spalten
                         .where(Device.token_hash == token_hash(auth[7:].strip()))).first()
    if dev is None or dev.revoked_at is not None:
        raise HTTPException(401, "Geräte-Token ungültig")
    state = db.get(BlocklistState, list_id)
    path = webfilter.list_file(list_id) if list_id in webfilter.BLOCKLISTS else None
    if state is None or not state.sha256 or path is None or not path.exists():
        raise HTTPException(404, "Liste nicht verfügbar")
    etag = f'"{state.sha256}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(path, media_type="text/plain", headers={"ETag": etag})


@router.post(p.ENROLL_PATH, response_model=p.EnrollResponse)
def enroll(req: p.EnrollRequest, request: Request, db: Session = Depends(get_platform_db)):
    ip = client_ip(request)
    if is_rate_limited(db, "enroll", ip):
        raise HTTPException(429, "Zu viele Fehlversuche")

    code = normalize_pairing_code(req.code)
    found = db.execute(select(PairingCode.id, PairingCode.family_id, PairingCode.used_at, PairingCode.expires_at)
                       .where(PairingCode.code_hash == token_hash(code))).first()   # Plattform: ohne Gerätenamen
    if found is None or found.used_at is not None or found.expires_at < utcnow():
        record_failure(db, "enroll", ip)
        raise HTTPException(400, "Kopplungscode ungültig oder abgelaufen")

    set_family(db, found.family_id)   # das Gerät gehört zur Familie, die den Code erzeugt hat
    # Code atomar einlösen: nur eine von mehreren gleichzeitigen Anfragen bekommt ihn
    claimed = db.execute(update(PairingCode).where(PairingCode.id == found.id, PairingCode.used_at.is_(None))
                         .values(used_at=utcnow()).returning(PairingCode.id)).first()
    if claimed is None:
        db.rollback()
        raise HTTPException(400, "Kopplungscode ungültig oder abgelaufen")
    pc = db.get(PairingCode, found.id)   # Gerätename ist mit dem Schlüssel dieser Familie verschlüsselt
    token = new_token()
    device = Device(name=pc.device_name, hostname=req.hostname, token_hash=token_hash(token))
    db.add(device)
    db.flush()
    pc.device_id = device.id
    db.commit()
    log.info("Gerät %s (%s) gekoppelt", device.id, req.hostname)
    return p.EnrollResponse(device_id=device.id, token=token)


def _device_for_token(db: Session, ws: WebSocket):
    """(id, family_id) des Geräts zum Token – im Plattformkontext nur unverschlüsselte Spalten."""
    auth = ws.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    dev = db.execute(select(Device.id, Device.family_id, Device.revoked_at)
                     .where(Device.token_hash == token_hash(auth[7:].strip()))).first()
    if dev is None or dev.revoked_at is not None:
        return None
    return dev


async def _send(ws: WebSocket, msg) -> None:
    await ws.send_text(msg.model_dump_json())


@router.websocket(p.WS_PATH)
async def agent_ws(ws: WebSocket):
    with platform_session() as db:   # Token-Prüfung über alle Familien, danach nur noch im Kontext des Geräts
        device = _device_for_token(db, ws)
        device_id = device.id if device else None
        family_id = device.family_id if device else None

    # Erst annehmen, dann ggf. mit Anwendungs-Code schließen – so kann der Agent
    # "Token ungültig/widerrufen" von Netzwerkfehlern unterscheiden.
    await ws.accept()
    if device_id is None:
        await _close(ws, p.CLOSE_UNAUTHORIZED, "Token ungültig")
        return

    # Handshake: erste Nachricht muss hello sein
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=HELLO_TIMEOUT)
        hello = p.Hello.model_validate_json(raw)
    except (asyncio.TimeoutError, ValidationError, WebSocketDisconnect):
        await _close(ws, p.CLOSE_PROTOCOL_ERROR, "hello erwartet")
        return

    supported = hello.protocol_version in p.SUPPORTED_VERSIONS
    with family_session(family_id) as db:
        dev = db.get(Device, device_id)
        dev.agent_version = hello.agent_version
        dev.hostname = hello.hostname
        dev.platform = hello.platform
        dev.protocol_version = hello.protocol_version
        dev.protocol_unsupported = not supported
        selfupdate.on_hello(dev, hello)
        dev.last_seen_at = utcnow()
        db.commit()

    if not supported:
        await _send(ws, p.Error(code="unsupported_version",
                                message=f"Server unterstützt {sorted(p.SUPPORTED_VERSIONS)}"))
        await _close(ws, p.CLOSE_UNSUPPORTED_VERSION)
        return

    old = await manager.register(device_id, ws)
    if old is not None:
        await _close(old, p.CLOSE_REPLACED, "neue Verbindung")

    await _send(ws, p.Welcome(
        protocol_version=hello.protocol_version, device_id=device_id,
        server_time=datetime.now(timezone.utc).isoformat()))
    log.info("Gerät %s verbunden (Agent %s, Protokoll %s)",
             device_id, hello.agent_version, hello.protocol_version)
    with family_session(family_id) as db:
        await policy.send_policy(db, device_id)
    if settings.relocate_to:   # Server-Umzug: der Agent prüft das Ziel selbst, bevor er wechselt
        await _send(ws, p.Relocate(server_url=settings.relocate_to))

    last_seen = utcnow()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = p.agent_message_adapter.validate_json(raw)
            except ValidationError:
                await _send(ws, p.Error(code="invalid_message"))
                await _close(ws, p.CLOSE_PROTOCOL_ERROR)
                return

            # DB-Arbeit im Worker-Thread, damit die Event-Loop alle anderen Verbindungen weiter bedient
            out = await asyncio.to_thread(_process, family_id, device_id, msg, last_seen)
            if out.revoked:
                await _close(ws, p.CLOSE_REVOKED)
                return
            last_seen = out.last_seen
            for m in out.reply:
                await _send(ws, m)
            for target, payload in out.push:
                try:
                    await manager.send(target, payload)
                except Exception:
                    log.warning("Zustellung an Gerät %s fehlgeschlagen", target, exc_info=True)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister(device_id, ws)
        with family_session(family_id) as db:
            dev = db.get(Device, device_id)
            if dev is not None:
                dev.last_seen_at = utcnow()
                db.commit()
        log.info("Gerät %s getrennt", device_id)


@dataclass
class _Outcome:
    """Ergebnis einer Agenten-Nachricht: Antworten an das Gerät und Nachrichten an andere Geräte."""
    last_seen: datetime
    revoked: bool = False
    reply: list = field(default_factory=list)
    push: list[tuple[int, dict]] = field(default_factory=list)


def _process(family_id: int, device_id: int, msg, last_seen: datetime) -> _Outcome:
    """Verarbeitet eine Agenten-Nachricht synchron (läuft per ``asyncio.to_thread``, sendet selbst nichts)."""
    out = _Outcome(last_seen=last_seen)
    with family_session(family_id) as db:
        dev = db.get(Device, device_id)
        if dev is None or dev.revoked_at is not None:
            out.revoked = True
            return out
        if utcnow() - last_seen >= LAST_SEEN_INTERVAL:
            out.last_seen = dev.last_seen_at = utcnow()
        if isinstance(msg, p.ReportUsers):
            _apply_user_report(dev, msg)
        elif isinstance(msg, p.ReportApps):
            programs.apply_app_report(db, dev, msg)
        elif isinstance(msg, p.BlockedReport):
            programs.apply_blocked_report(db, dev, msg)
        elif isinstance(msg, p.AppUsageReport):
            stats.apply_app_report(db, dev, msg)
            nowplaying.update(dev.id, msg.playing, db)
        elif isinstance(msg, p.IntegrityReport):
            integrity.alarm(dev, integrity.apply_report(dev, msg))
        elif isinstance(msg, p.UpdateStatus):
            selfupdate.on_status(dev, msg)
        db.commit()

        if isinstance(msg, p.Ping):
            out.reply.append(p.Pong())
        elif isinstance(msg, (p.ChatSend, p.ExtensionRequest, p.AccessRequest)):
            if isinstance(msg, p.AccessRequest):
                ack, new = access.receive(db, device_id, msg)
            else:
                ack, new = chat.receive_from_agent(db, device_id, msg)
            out.reply.append(ack)
            if new is not None:
                out.push += chat.child_push_payloads(db, new)
                chat.notify_parents_new(db, new)
        elif isinstance(msg, p.PolicyAck):
            dev.policy_version = msg.version
            db.commit()
        elif isinstance(msg, p.UsageReport):
            changed = policy.record_usage(db, device_id, msg)
            out.push += policy.usage_update_payloads(db, changed, device_id)
        elif isinstance(msg, p.ChatSync):
            chat.expire_stale_requests(db)
            out.reply += [chat.to_wire(db, m) for m in chat.history_for_agent(db, device_id, msg)]
    return out


def _apply_user_report(dev: Device, msg: p.ReportUsers) -> None:
    """Upsert der gemeldeten Benutzer; Zuordnungen bleiben auch für verschwundene User erhalten."""
    reported = {u.username: u for u in msg.users}
    existing = {du.username: du for du in dev.users}
    for name, du in existing.items():
        u = reported.get(name)
        du.present = u is not None
        if u is not None:
            du.uid, du.full_name = u.uid, u.full_name
    for name, u in reported.items():
        if name not in existing:
            dev.users.append(DeviceUser(username=name, uid=u.uid, full_name=u.full_name))


async def _close(ws: WebSocket, code: int, reason: str = "") -> None:
    try:
        await ws.close(code=code, reason=reason)
    except Exception:
        pass
