"""Software-Authenticator für Tests: erzeugt Registrierungs- und Anmeldeantworten wie ein Browser mit Passkey
(ES256, Attestation „none“). Origin, RP-ID, Zähler und Backup-Flag lassen sich für Fehlerfälle verfälschen."""

from __future__ import annotations

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftAuthenticator:
    def __init__(self, rp_id: str = "testserver", origin: str = "http://testserver", backed_up: bool = False,
                 counter: int = 0) -> None:
        self.rp_id, self.origin, self.backed_up, self.counter = rp_id, origin, backed_up, counter
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.user_handle = b""

    def _flags(self, extra: int = 0) -> int:
        return UP | UV | extra | ((BE | BS) if self.backed_up else 0)

    def _client_data(self, typ: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": typ, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def create(self, options_json: str, origin: str | None = None, rp_id: str | None = None) -> str:
        o = json.loads(options_json)
        self.user_handle = base64.urlsafe_b64decode(o["user"]["id"] + "==")
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        auth = (hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([self._flags(AT)])
                + self.counter.to_bytes(4, "big") + bytes(16) + len(self.cred_id).to_bytes(2, "big") + self.cred_id + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        client = self._client_data("webauthn.create", o["challenge"], origin)
        return json.dumps({"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                           "response": {"clientDataJSON": b64u(client), "attestationObject": b64u(att),
                                        "transports": ["internal"]},
                           "clientExtensionResults": {}})

    def get(self, options_json: str, origin: str | None = None, rp_id: str | None = None,
            counter: int | None = None) -> str:
        o = json.loads(options_json)
        if counter is None:
            self.counter += 1 if not self.backed_up else 0
            counter = self.counter
        auth = hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([self._flags()]) + counter.to_bytes(4, "big")
        client = self._client_data("webauthn.get", o["challenge"], origin)
        sig = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return json.dumps({"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                           "response": {"clientDataJSON": b64u(client), "authenticatorData": b64u(auth),
                                        "signature": b64u(sig), "userHandle": b64u(self.user_handle)},
                           "clientExtensionResults": {}})
