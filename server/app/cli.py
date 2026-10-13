"""CLI: Bootstrap des ersten Eltern-Accounts.

Interaktiv:   python -m app.cli create-parent
Per Env-Var:  KIDCTL_BOOTSTRAP_USER=… KIDCTL_BOOTSTRAP_PASSWORD=… python -m app.cli create-parent
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import qrcode
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


def main() -> int:
    ap = argparse.ArgumentParser(prog="kidctl-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("create-parent", help="Eltern-Account anlegen (Bootstrap)")
    cp.add_argument("--force", action="store_true", help="auch wenn bereits Accounts existieren")
    cp.set_defaults(func=create_parent)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
