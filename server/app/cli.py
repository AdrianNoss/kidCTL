"""CLI: Bootstrap des ersten Eltern-Accounts, Notfall-Zurücksetzen, VAPID-Schlüssel.

Interaktiv:   python -m app.cli create-parent
Per Env-Var:  KIDCTL_BOOTSTRAP_USER=… [KIDCTL_BOOTSTRAP_EMAIL=…] KIDCTL_BOOTSTRAP_PASSWORD=… python -m app.cli create-parent
              (im SaaS-Betrieb ist die E-Mail Pflicht und ersetzt den Benutzernamen)
Notfall:      python -m app.cli reset-password <benutzer> | reset-totp <benutzer>
VAPID:        python -m app.cli vapid-keys >> .env
Hauptschlüssel: python -m app.cli master-key >> .env   (einmalig; getrennt sichern!)
Umstieg:      python -m app.cli import-sqlite /data/kidctl.db [--apply]   (SQLite bis 0.32.x -> PostgreSQL)
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys

import qrcode
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from .db import init_db
from .tenant import platform_session, set_family
from .accounts import end_sessions, password_error
from .models import ROLE_OWNER, ROLE_PARENT, Family, Membership, Parent, utcnow
from .security import hash_password, new_totp_secret, totp_uri


def create_parent(args: argparse.Namespace) -> int:
    init_db()
    from .config import settings
    email = os.environ.get("KIDCTL_BOOTSTRAP_EMAIL", "")
    if settings.saas and not email:
        email = input("E-Mail-Adresse: ").strip()
    username = os.environ.get("KIDCTL_BOOTSTRAP_USER") or (email.split("@")[0] if settings.saas and email else "") \
        or input("Benutzername: ").strip()
    password = os.environ.get("KIDCTL_BOOTSTRAP_PASSWORD")
    if not password:
        password = getpass.getpass("Passwort (min. 12 Zeichen): ")
        if password != getpass.getpass("Passwort wiederholen: "):
            print("Passwörter stimmen nicht überein.", file=sys.stderr)
            return 1
    if len(password) < 12 or len(password.encode()) > 72:
        print("Passwort muss 12 Zeichen bis 72 Bytes lang sein.", file=sys.stderr)
        return 1

    with platform_session() as db:
        if db.scalar(select(Parent.id).where(Parent.username == username)):
            print(f"Benutzer {username!r} existiert bereits.", file=sys.stderr)
            return 1
        if db.scalar(select(Parent.id).limit(1)) and not args.force:
            print("Es existiert bereits ein Eltern-Account. Weitere im WebUI anlegen "
                  "(oder --force).", file=sys.stderr)
            return 1
        from .accounts import email_error
        if err := email_error(email, db):
            print(err, file=sys.stderr)
            return 1
        # Einzelbetrieb: erste Familie anlegen bzw. die vorhandene nutzen; der erste Account wird Inhaber
        family_id = db.scalar(select(Family.id).order_by(Family.id).limit(1))
        if family_id is None:
            family = Family()
            db.add(family)
            db.flush()
            family_id = family.id
        owner = db.scalar(select(Membership.id).where(Membership.family_id == family_id, Membership.role == ROLE_OWNER))
        set_family(db, family_id)   # E-Mail wird mit dem Schlüssel der Familie verschlüsselt
        parent = Parent(username=username, password_hash=hash_password(password), totp_secret=new_totp_secret(),
                        email=email, email_verified_at=utcnow() if email else None)   # vom Betreiber angelegt
        db.add(parent)
        db.flush()
        db.add(Membership(parent_id=parent.id, role=ROLE_PARENT if owner else ROLE_OWNER))
        db.commit()
        uri = totp_uri(parent)

    print(f"\nEltern-Account {username!r} angelegt.")
    _print_totp(uri, parent.totp_secret)
    return 0


def _print_totp(uri: str, secret: str) -> None:
    print("TOTP in der Authenticator-App einrichten (QR-Code oder Secret):\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(invert=True)
    print(f"\nSecret: {secret}\nURI:    {uri}")


def _parent(db, username: str) -> Parent | None:
    from .tenant import load_parent
    pid = db.scalar(select(Parent.id).where(Parent.username == username))
    parent = load_parent(db, pid) if pid is not None else None
    if parent is None:
        print(f"Benutzer {username!r} gibt es nicht.", file=sys.stderr)
    return parent


def reset_password(args: argparse.Namespace) -> int:
    """Notfall: Passwort neu setzen (alle Sitzungen des Accounts enden)."""
    init_db()
    password = getpass.getpass("Neues Passwort (min. 12 Zeichen): ")
    if err := password_error(password, getpass.getpass("Wiederholen: ")):
        print(err, file=sys.stderr)
        return 1
    with platform_session() as db:
        if (parent := _parent(db, args.username)) is None:
            return 1
        parent.password_hash = hash_password(password)
        end_sessions(db, parent.id)
        db.commit()
    print(f"Passwort für {args.username!r} gesetzt.")
    return 0


def reset_totp(args: argparse.Namespace) -> int:
    """Notfall: neuen Authenticator erzeugen (alter wird ungültig, alle Sitzungen enden)."""
    init_db()
    with platform_session() as db:
        if (parent := _parent(db, args.username)) is None:
            return 1
        parent.totp_secret, parent.totp_last_step = new_totp_secret(), 0
        end_sessions(db, parent.id)
        db.commit()
        uri, secret = totp_uri(parent), parent.totp_secret
    print(f"Neuer Authenticator für {args.username!r}:")
    _print_totp(uri, secret)
    return 0


def vapid_keys(_args: argparse.Namespace) -> int:
    """Erzeugt ein VAPID-Schlüsselpaar für Web Push (Ausgabe für .env)."""
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    print(f"VAPID_PRIVATE_KEY={b64(priv)}\nVAPID_PUBLIC_KEY={b64(pub)}")
    print("VAPID_SUBJECT=mailto:you@example.org  # anpassen", file=sys.stderr)
    return 0


def backup(_args: argparse.Namespace) -> int:
    """Datenbank-Backup sofort erstellen (wie der tägliche Lauf)."""
    from . import backup as bk
    info = bk.run()
    print(f"Backup: {info.path} ({info.size} Bytes)")
    return 0


def master_key(_args: argparse.Namespace) -> int:
    """Neuen Hauptschlüssel für .env ausgeben (verschlüsselt die Schlüssel der Familien)."""
    from .crypto import new_master_key
    print(f"KIDCTL_MASTER_KEY={new_master_key()}")
    print("Getrennt sichern (z. B. Passwortmanager) – ohne ihn sind die verschlüsselten Daten verloren.", file=sys.stderr)
    return 0


def import_sqlite(args: argparse.Namespace) -> int:
    """Alte SQLite-Datenbank nach PostgreSQL übernehmen (ohne --apply nur Probelauf)."""
    from .db import engine
    from .sqlite_import import import_sqlite as run

    try:
        counts = run(args.path, engine, apply=args.apply)
    except RuntimeError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    for name, n in counts.items():
        if n:
            print(f"  {name:<22} {n:>7}")
    total = sum(counts.values())
    print(f"{'Übernommen' if args.apply else '[dry-run] Würde übernehmen'}: {total} Zeilen aus {args.path}")
    if not args.apply:
        print("Mit --apply ausführen. Die SQLite-Datei bleibt unverändert.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="kidctl-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("create-parent", help="Eltern-Account anlegen (Bootstrap)")
    cp.add_argument("--force", action="store_true", help="auch wenn bereits Accounts existieren")
    cp.set_defaults(func=create_parent)
    rp = sub.add_parser("reset-password", help="Notfall: Passwort eines Eltern-Accounts neu setzen")
    rp.add_argument("username")
    rp.set_defaults(func=reset_password)
    rt = sub.add_parser("reset-totp", help="Notfall: neuen Authenticator für einen Eltern-Account erzeugen")
    rt.add_argument("username")
    rt.set_defaults(func=reset_totp)
    vk = sub.add_parser("vapid-keys", help="VAPID-Schlüssel für Web Push erzeugen (für .env)")
    vk.set_defaults(func=vapid_keys)
    bp = sub.add_parser("backup", help="Datenbank-Backup jetzt erstellen (nach <data>/backups)")
    bp.set_defaults(func=backup)
    mk = sub.add_parser("master-key", help="Hauptschlüssel für die Verschlüsselung erzeugen (für .env)")
    mk.set_defaults(func=master_key)
    ip = sub.add_parser("import-sqlite", help="SQLite-Datenbank (bis 0.32.x) nach PostgreSQL übernehmen")
    ip.add_argument("path", help="Pfad zur kidctl.db")
    ip.add_argument("--apply", action="store_true", help="wirklich übernehmen (sonst Probelauf)")
    ip.set_defaults(func=import_sqlite)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
