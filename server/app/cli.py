"""CLI: Bootstrap des ersten Eltern-Accounts, Notfall-Zurücksetzen, VAPID-Schlüssel.

Interaktiv:   python -m app.cli create-parent
Per Env-Var:  KIDCTL_BOOTSTRAP_USER=… KIDCTL_BOOTSTRAP_PASSWORD=… python -m app.cli create-parent
Notfall:      python -m app.cli reset-password <benutzer> | reset-totp <benutzer>
VAPID:        python -m app.cli vapid-keys >> .env
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

from .db import SessionLocal, init_db
from .accounts import end_sessions, password_error
from .models import Parent
from .security import hash_password, new_totp_secret, totp_uri


def create_parent(args: argparse.Namespace) -> int:
    init_db()
    username = os.environ.get("KIDCTL_BOOTSTRAP_USER") or input("Benutzername: ").strip()
    password = os.environ.get("KIDCTL_BOOTSTRAP_PASSWORD")
    if not password:
        password = getpass.getpass("Passwort (min. 12 Zeichen): ")
        if password != getpass.getpass("Passwort wiederholen: "):
            print("Passwörter stimmen nicht überein.", file=sys.stderr)
            return 1
    if len(password) < 12 or len(password.encode()) > 72:
        print("Passwort muss 12 Zeichen bis 72 Bytes lang sein.", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        if db.scalar(select(Parent).where(Parent.username == username)):
            print(f"Benutzer {username!r} existiert bereits.", file=sys.stderr)
            return 1
        if db.scalar(select(Parent).limit(1)) and not args.force:
            print("Es existiert bereits ein Eltern-Account. Weitere im WebUI anlegen "
                  "(oder --force).", file=sys.stderr)
            return 1
        parent = Parent(username=username, password_hash=hash_password(password),
                        totp_secret=new_totp_secret())
        db.add(parent)
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
    parent = db.scalar(select(Parent).where(Parent.username == username))
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
    with SessionLocal() as db:
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
    with SessionLocal() as db:
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
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
