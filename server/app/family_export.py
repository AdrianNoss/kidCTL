"""Export und Import der Daten einer Familie (Etappe 4.5) – auch für den Wechsel zwischen Self-Hosting und SaaS.

Format: ZIP mit ``manifest.json`` (Format, Version, Zeilen je Tabelle) und je Tabelle einer JSON-Datei mit allen Zeilen,
**entschlüsselt** mit dem Schlüssel der Familie. Beim Import werden die Daten mit dem Schlüssel der Zielfamilie neu
verschlüsselt und bekommen neue IDs (Verweise werden umgeschrieben).

- Enthalten: Kinder mit allen Regeln, Programmen, Web-Einstellungen, Geräte und ihre Benutzer, Nutzung, Protokoll,
  Chat. **Nicht** enthalten: Eltern-Accounts (Passwörter, Authenticator), Einladungen, Kopplungscodes, Support-Daten.
- Geräte kommen als getrennt an: Ihr Token gilt nur auf dem alten Server, also neu koppeln. Verweise auf Eltern
  (z. B. wer eine Nachricht geschrieben hat) entfallen.
- Import nur in eine leere Familie (keine Kinder, keine Geräte).
- Versionen: ältere Exporte bleiben lesbar (fehlende Spalten bekommen Standardwerte, unbekannte werden ignoriert).
"""

from __future__ import annotations

import io
import json
import secrets
import zipfile
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, Integer, Time, func, select
from sqlalchemy.orm import Session

from .db import Base
from .models import Child, Device, FamilyScoped, utcnow

FORMAT = "kidctl-family-export"
VERSION = 1
EXCLUDED = {"membership", "family_invite", "pairing_code", "support_grant", "support_access", "billing_address",
            "ticket", "ticket_message"}
MAX_ZIP = 200 * 1024 * 1024       # Schutz vor zu großen Uploads/Archiven (entpackt)


class ImportError_(Exception):
    """Export-Datei passt nicht (Format, Version, Inhalt) oder Zielfamilie nicht leer."""


def _models() -> list[type]:
    """Familientabellen in Abhängigkeitsreihenfolge (Eltern vor Kindern)."""
    by_table = {m.local_table: m.class_ for m in Base.registry.mappers if issubclass(m.class_, FamilyScoped)}
    return [by_table[t] for t in Base.metadata.sorted_tables if t in by_table and t.name not in EXCLUDED]


def _dump(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _load(column, value):
    if value is None:
        return None
    t = column.type
    if isinstance(t, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(t, Date):
        return date.fromisoformat(value)
    if isinstance(t, Time):
        return time.fromisoformat(value)
    if isinstance(t, Boolean):
        return bool(value)
    if isinstance(t, Integer):
        return int(value)
    return str(value)


def export_zip(db: Session) -> bytes:
    """ZIP der Familie im Kontext (``db`` mit Familienkontext)."""
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for model in _models():
            cols = [c for c in model.__table__.columns if c.name != "family_id"]
            rows = [{c.name: _dump(getattr(obj, c.name)) for c in cols}
                    for obj in db.scalars(select(model).order_by(model.id))]
            counts[model.__tablename__] = len(rows)
            zf.writestr(f"{model.__tablename__}.json", json.dumps(rows, ensure_ascii=False, indent=1))
        manifest = {"format": FORMAT, "version": VERSION, "exported_at": utcnow().isoformat(),
                    "tables": counts}
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
    return buf.getvalue()


def is_empty(db: Session) -> bool:
    return not (db.scalar(select(func.count(Child.id))) or db.scalar(select(func.count(Device.id))))


def import_zip(db: Session, data: bytes) -> dict[str, int]:
    """Export in die (leere) Familie im Kontext einspielen; Rückgabe: Zeilen je Tabelle."""
    if not is_empty(db):
        raise ImportError_("not_empty")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in zf.infolist()) > MAX_ZIP:
            raise ImportError_("too_large")
        manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise ImportError_("format") from e
    if manifest.get("format") != FORMAT:
        raise ImportError_("format")
    if not isinstance(manifest.get("version"), int) or manifest["version"] > VERSION:
        raise ImportError_("version")

    models = _models()
    ids: dict[str, dict[int, int]] = {}
    counts = {}
    try:
        for model in models:
            name = model.__tablename__
            try:
                rows = json.loads(zf.read(f"{name}.json"))
            except KeyError:
                rows = []                       # Tabelle gab es in dieser Version noch nicht
            cols = {c.name: c for c in model.__table__.columns if c.name not in ("id", "family_id")}
            fks = {}
            for c in cols.values():
                for fk in c.foreign_keys:
                    fks[c.name] = fk.column.table.name
            mapping = ids.setdefault(name, {})
            for row in rows:
                values = {}
                for key, column in cols.items():
                    if key not in row:
                        continue                # fehlende Spalte: Standardwert des Modells
                    value = _load(column, row[key])
                    if key in fks and value is not None:
                        target = fks[key]
                        if target in ids:
                            value = ids[target].get(value)
                            if value is None and not column.nullable:
                                break           # Verweis ins Leere: Zeile auslassen
                        else:
                            value = None        # Eltern-Accounts u. Ä. werden nicht übernommen
                    values[key] = value
                else:
                    if model is Device:         # Token gilt nur auf dem alten Server: getrennt übernehmen
                        values["token_hash"] = "import-" + secrets.token_hex(28)
                        values["revoked_at"] = values.get("revoked_at") or utcnow()
                    obj = model(**values)
                    db.add(obj)
                    db.flush()
                    mapping[int(row["id"])] = obj.id
                    counts[name] = counts.get(name, 0) + 1
                    continue
        db.commit()
    except ImportError_:
        db.rollback()
        raise
    except Exception as e:                      # beschädigte oder manipulierte Daten: nichts übernehmen
        db.rollback()
        raise ImportError_("content") from e
    return counts
