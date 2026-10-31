"""Symmetric crypto helpers: Fernet for secrets at rest, HMAC signing, password hashing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _derive(secret: str, purpose: str) -> bytes:
    return hashlib.sha256(f"streamhub:{purpose}:{secret}".encode()).digest()


class Crypto:
    def __init__(self, secret_key: str) -> None:
        self._fernet = Fernet(base64.urlsafe_b64encode(_derive(secret_key, "fernet")))
        self._sign_key = _derive(secret_key, "sign")
        self.jwt_key = base64.urlsafe_b64encode(_derive(secret_key, "jwt")).decode()

    # ---- Fernet (opaque, authenticated)
    def encrypt(self, data: bytes) -> str:
        return self._fernet.encrypt(data).decode()

    def decrypt(self, token: str, ttl: int | None = None) -> bytes:
        return self._fernet.decrypt(token.encode(), ttl=ttl)

    def encrypt_json(self, obj: Any) -> str:
        return self.encrypt(json.dumps(obj, separators=(",", ":")).encode())

    def decrypt_json(self, token: str, ttl: int | None = None) -> Any:
        return json.loads(self.decrypt(token, ttl=ttl))

    def try_decrypt_json(self, token: str, ttl: int | None = None) -> Any | None:
        try:
            return self.decrypt_json(token, ttl=ttl)
        except (InvalidToken, ValueError):
            return None

    # ---- deterministic signed payloads (cacheable URLs, non-secret contents)
    def sign_json(self, obj: Any) -> str:
        body = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=")
        sig = hmac.new(self._sign_key, body, hashlib.sha256).digest()[:16]
        return f"{body.decode()}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"

    def verify_json(self, token: str) -> Any | None:
        try:
            body, sig = token.split(".", 1)
            expected = hmac.new(self._sign_key, body.encode(), hashlib.sha256).digest()[:16]
            got = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
            if not hmac.compare_digest(expected, got):
                return None
            return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, TypeError):
            return None


# ---- password / PIN hashing (stdlib scrypt)
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_secret(value: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_secret(value: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, salt_hex, digest_hex = stored.split("$", 2)
        digest = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt_hex), dklen=32, **_SCRYPT)
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except ValueError:
        return False
