"""CLI: Bootstrap des ersten Eltern-Accounts, VAPID-Schlüssel.

Interaktiv:   python -m app.cli create-parent
Per Env-Var:  KIDCTL_BOOTSTRAP_USER=… KIDCTL_BOOTSTRAP_PASSWORD=… python -m app.cli create-parent
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
    print("TOTP in der Authenticator-App einrichten (QR-Code oder Secret):\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(invert=True)
    print(f"\nSecret: {parent.totp_secret}\nURI:    {uri}")
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


def main() -> int:
    ap = argparse.ArgumentParser(prog="kidctl-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("create-parent", help="Eltern-Account anlegen (Bootstrap)")
    cp.add_argument("--force", action="store_true", help="auch wenn bereits Accounts existieren")
    cp.set_defaults(func=create_parent)
    vk = sub.add_parser("vapid-keys", help="VAPID-Schlüssel für Web Push erzeugen (für .env)")
    vk.set_defaults(func=vapid_keys)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
