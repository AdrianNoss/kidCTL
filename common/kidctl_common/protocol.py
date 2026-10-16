"""WebSocket-Protokoll Agent <-> Server.

Jede Nachricht ist ein JSON-Objekt mit Feld ``type``. Der Agent sendet als erste Nachricht
``hello`` mit seiner Protokollversion; der Server antwortet mit ``welcome`` oder ``error``.

Versionierung: ``PROTOCOL_VERSION`` ist die aktuelle Version. Der Server akzeptiert alle
Versionen in ``SUPPORTED_VERSIONS`` (Ziel: aktuelle + mindestens eine ältere).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter

# Strenge Formate für Werte, die der Agent in Dateien, Pfade oder PAM-Listen schreibt – ein
# fehlerhafter oder kompromittierter Server darf darüber nichts außerhalb der Policy bewirken
# (z. B. Zeilenumbruch im Benutzernamen -> weitere Einträge in der PAM-Sperrliste).
USERNAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}\$?$"
UserName = Annotated[str, StringConstraints(pattern=USERNAME_PATTERN)]
# Kind-IDs bestimmen den Resolver-Port (5300 + ID) -> nach oben begrenzt
ChildId = Annotated[int, Field(ge=1, le=60000)]

PROTOCOL_VERSION = 1
# Bei Version 2 hier {1, 2} eintragen – mindestens eine ältere Version bleibt unterstützt.
SUPPORTED_VERSIONS = frozenset({1})

WS_PATH = "/api/v1/ws"
ENROLL_PATH = "/api/v1/enroll"
LISTS_PATH = "/api/v1/lists"  # GET {LISTS_PATH}/<id>, Authorization: Bearer <Geräte-Token>

# WebSocket-Close-Codes (4000–4999 anwendungsspezifisch)
CLOSE_UNAUTHORIZED = 4401
CLOSE_REVOKED = 4403
CLOSE_UNSUPPORTED_VERSION = 4426
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_REPLACED = 4409


# ---------- Kopplung (HTTP) ----------

class EnrollRequest(BaseModel):
    code: str = Field(min_length=8, max_length=16)
    hostname: str = Field(min_length=1, max_length=255)


class EnrollResponse(BaseModel):
    device_id: int
    token: str
    protocol_version: int = PROTOCOL_VERSION


# ---------- WebSocket-Nachrichten ----------

class Hello(BaseModel):
    """Agent -> Server, erste Nachricht nach dem Verbindungsaufbau."""
    type: Literal["hello"] = "hello"
    protocol_version: int
    agent_version: str = Field(max_length=64)
    hostname: str = Field(max_length=255)


class Welcome(BaseModel):
    """Server -> Agent, Antwort auf ``hello``."""
    type: Literal["welcome"] = "welcome"
    protocol_version: int
    device_id: int
    server_time: str  # ISO 8601, UTC


class Error(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str = ""


class Ping(BaseModel):
    type: Literal["ping"] = "ping"


class Pong(BaseModel):
    type: Literal["pong"] = "pong"


class LocalUser(BaseModel):
    username: UserName
    uid: int = Field(ge=1000)
    full_name: str = Field(default="", max_length=255)


class ReportUsers(BaseModel):
    """Agent -> Server: lokale Benutzer mit UID >= 1000."""
    type: Literal["report_users"] = "report_users"
    users: list[LocalUser] = Field(max_length=500)


# ---------- Chat & Verlängerungen ----------
# Der Agent leitet Nachrichten der Tray-App weiter und puffert sie offline. ``client_msg_id``
# macht das Senden idempotent: Der Server bestätigt jede Nachricht mit ``ack`` (auch Duplikate),
# erst danach entfernt der Agent sie aus seiner Warteschlange.

MAX_BODY = 2000


class ChatSend(BaseModel):
    """Agent -> Server: Textnachricht eines Kindes (``username`` = Linux-User)."""
    type: Literal["chat_send"] = "chat_send"
    client_msg_id: str = Field(min_length=8, max_length=64)
    username: str = Field(max_length=64)
    body: str = Field(min_length=1, max_length=MAX_BODY)


class ExtensionRequest(BaseModel):
    """Agent -> Server: Verlängerungsanfrage (mehr Minuten und/oder späteres Fensterende)."""
    type: Literal["extension_request"] = "extension_request"
    client_msg_id: str = Field(min_length=8, max_length=64)
    username: str = Field(max_length=64)
    day: date  # lokaler Kalendertag, für den die Verlängerung gelten soll
    minutes: int = Field(default=0, ge=0, le=720)
    window_end: time | None = None
    reason: str = Field(min_length=1, max_length=MAX_BODY)


class ChatSync(BaseModel):
    """Agent -> Server: Verlauf ab ``after_id`` für einen Linux-User anfordern."""
    type: Literal["chat_sync"] = "chat_sync"
    username: str = Field(max_length=64)
    after_id: int = Field(default=0, ge=0)


class Ack(BaseModel):
    """Server -> Agent: Bestätigung für ``chat_send``/``extension_request``.

    ``ok=False`` mit ``error`` = endgültig abgelehnt (z. B. User keinem Kind zugeordnet);
    der Agent verwirft die Nachricht dann ebenfalls.
    """
    type: Literal["ack"] = "ack"
    client_msg_id: str
    ok: bool
    message_id: int | None = None
    error: str = ""


class ChatMessage(BaseModel):
    """Server -> Agent: Chat-Eintrag (neu oder aktualisiert, z. B. Entscheidung einer Anfrage).

    Der Agent ersetzt Einträge mit gleicher ``id`` (Upsert).
    """
    type: Literal["chat_message"] = "chat_message"
    id: int
    child_id: int
    from_child: bool
    sender_name: str
    kind: Literal["text", "extension_request"]
    body: str
    created_at: datetime  # UTC
    request_day: date | None = None
    requested_minutes: int | None = None
    requested_window_end: time | None = None
    status: Literal["pending", "approved", "rejected", "expired"] | None = None
    approved_minutes: int | None = None
    approved_window_end: time | None = None


# ---------- Policy & Nutzungszeit ----------
# Der Server sendet nach ``welcome`` und nach jeder Änderung die vollständige ``policy`` des
# Geräts. ``version`` ist ein Hash des Inhalts; der Agent bestätigt mit ``policy_ack``, cacht die
# Policy lokal und setzt sie auch offline durch. Linux-User, die nicht in ``users`` stehen, sind
# nicht eingeschränkt.
#
# Nutzungszeit: Der Agent meldet je (Linux-User, Tag) seine *absolute* lokal gezählte Zeit
# (idempotent, Server übernimmt das Maximum). Für die geräteübergreifende Summe sendet der Server
# je Kind ``usage_update`` mit der Zeit *aller anderen* Geräte; effektive Nutzung = eigene
# Zählung + ``others_seconds``.


class DayRule(BaseModel):
    weekday: int = Field(ge=0, le=6)  # 0 = Montag
    quota_minutes: int = Field(ge=0)
    window_start: time
    window_end: time


class DayOverride(BaseModel):
    day: date
    extra_minutes: int = Field(default=0, ge=0)
    window_end_override: time | None = None


ProgramMode = Literal["off", "audit", "enforce"]
WebMode = Literal["off", "whitelist", "blacklist"]


class BlocklistRef(BaseModel):
    """Sperrliste, die der Agent unter ``LISTS_PATH/<id>`` abholt (eine Domain je Zeile)."""
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")  # auch Dateiname im Cache
    sha256: str = ""  # leer = auf dem Server noch nicht verfügbar
    name: str = ""
    report: bool = True  # Treffer ins Sperrprotokoll (False bei Werbe-/Tracker-Listen: zu viele)


class WebFilter(BaseModel):
    """Website-Filter eines Kindes. ``allow`` enthält bereits die Domains gewählter Bundles.
    Domains gelten inkl. aller Subdomains."""
    mode: WebMode = "off"
    allow: list[str] = []
    deny: list[str] = []
    lists: list[BlocklistRef] = []   # nur im Blacklist-Modus relevant
    safesearch: bool = True          # Google/Bing/DuckDuckGo SafeSearch, YouTube eingeschränkt


class ChildPolicy(BaseModel):
    id: ChildId
    name: str
    rules: list[DayRule]
    exceptions: list[DayOverride] = []
    # Programm-Whitelist: off = keine Kontrolle, audit = nur protokollieren, enforce = sperren
    program_mode: ProgramMode = "off"
    allowed_programs: list[str] = []  # App-Schlüssel (siehe AppEntry.key), zusätzlich zur Basisliste
    web: WebFilter = WebFilter()
    # „Jetzt sperren“ der Eltern: gesperrt bis ``locked_until`` (UTC) bzw. bis zur Freigabe (None).
    # Läuft auch offline ab – der Agent vergleicht mit der Systemuhr.
    locked: bool = False
    locked_until: datetime | None = None
    lock_grace_seconds: int = Field(default=60, ge=0, le=7200)  # Vorwarnung bis zur Abmeldung


class Policy(BaseModel):
    """Server -> Agent: vollständige Policy für dieses Gerät."""
    type: Literal["policy"] = "policy"
    version: str
    timezone: str  # Tageswechsel/Zeitfenster in dieser Zone (z. B. Europe/Berlin)
    users: dict[UserName, ChildId]  # Linux-Username -> Kind-ID
    children: list[ChildPolicy]


class PolicyAck(BaseModel):
    """Agent -> Server: Policy ``version`` übernommen und gecacht."""
    type: Literal["policy_ack"] = "policy_ack"
    version: str = Field(max_length=64)


class UsageEntry(BaseModel):
    username: str = Field(max_length=64)
    day: date
    seconds: int = Field(ge=0, le=2 * 86400)


class UsageReport(BaseModel):
    """Agent -> Server: absolute Nutzungszeit je (User, Tag) auf diesem Gerät."""
    type: Literal["usage_report"] = "usage_report"
    entries: list[UsageEntry] = Field(max_length=200)


class UsageUpdate(BaseModel):
    """Server -> Agent: Nutzungszeit des Kindes an ``day`` auf allen *anderen* Geräten."""
    type: Literal["usage_update"] = "usage_update"
    child_id: int
    day: date
    others_seconds: int


# ---------- Programme ----------
# App-Schlüssel: "exe:<realpath>" (Pacman/AUR, Binary des .desktop-Eintrags) oder
# "flatpak:<app-id>" (systemweites Flatpak).


class AppEntry(BaseModel):
    key: str = Field(max_length=512)
    name: str = Field(max_length=255)
    icon: str = Field(default="", max_length=255)
    desktop_id: str = Field(default="", max_length=255)


class ReportApps(BaseModel):
    """Agent -> Server: installierte Anwendungen (systemweite .desktop-Einträge)."""
    type: Literal["report_apps"] = "report_apps"
    apps: list[AppEntry] = Field(max_length=2000)


class BlockedEvent(BaseModel):
    username: str = Field(max_length=64)
    at: datetime  # UTC
    kind: Literal["program", "domain"] = "program"
    key: str = Field(max_length=512)    # App-Schlüssel, "exe:<pfad>" bzw. "domain:<name>"
    name: str = Field(max_length=255)   # Anzeigename (aus .desktop oder Dateiname)
    detail: str = Field(default="", max_length=255)  # z. B. „interaktive Shell“
    audit: bool = False                 # nur protokolliert, nicht gesperrt


class BlockedReport(BaseModel):
    """Agent -> Server: gesperrte (bzw. im Protokollmodus erkannte) Aktionen."""
    type: Literal["blocked"] = "blocked"
    events: list[BlockedEvent] = Field(max_length=500)


# ---------- Programmzeit ----------
# Wie lange eine App während gezählter Bildschirmzeit geöffnet war (parallel geöffnete Apps
# zählen jeweils). Absolute Zählerstände je (User, Tag, App) wie bei usage_report.


class AppUsageEntry(BaseModel):
    username: str = Field(max_length=64)
    day: date
    key: str = Field(max_length=512)
    seconds: int = Field(ge=0, le=2 * 86400)


class AppUsageReport(BaseModel):
    type: Literal["app_usage_report"] = "app_usage_report"
    entries: list[AppUsageEntry] = Field(max_length=2000)


# ---------- Integrität ----------
# Der Agent prüft regelmäßig, ob seine Schutzmaßnahmen intakt sind, repariert, was er selbst
# verwaltet (nftables, Resolver, Mounts), und meldet den Zustand bei Änderung und nach jedem
# Verbindungsaufbau. Der Server alarmiert die Eltern bei neu fehlschlagenden Prüfungen.


class IntegrityCheck(BaseModel):
    id: str = Field(max_length=64)       # z. B. "pam", "nsswitch", "dns:1"
    label: str = Field(max_length=255)
    ok: bool
    detail: str = Field(default="", max_length=500)


class IntegrityReport(BaseModel):
    type: Literal["integrity"] = "integrity"
    checks: list[IntegrityCheck] = Field(max_length=100)


AgentMessage = Annotated[
    Union[Hello, Ping, Pong, ReportUsers, ChatSend, ExtensionRequest, ChatSync, PolicyAck,
          UsageReport, ReportApps, BlockedReport, IntegrityReport, AppUsageReport],
    Field(discriminator="type")]
ServerMessage = Annotated[
    Union[Welcome, Error, Ping, Pong, Ack, ChatMessage, Policy, UsageUpdate],
    Field(discriminator="type")]

agent_message_adapter: TypeAdapter[AgentMessage] = TypeAdapter(AgentMessage)
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
