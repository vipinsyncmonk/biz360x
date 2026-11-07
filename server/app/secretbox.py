"""Field-level encryption for sensitive columns (`*_enc`), e.g. an employee's bank account number.

Fernet (AES-128-CBC + HMAC) with a key from BIZ_FIELD_KEY. In dev a fixed key is derived so tests run; anywhere
else a missing key is an error. Production should supply the key from a KMS / secret manager and rotate it with
MultiFernet; nothing here is a substitute for that.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _key() -> bytes:
    k = os.environ.get("BIZ_FIELD_KEY")
    if k:
        return k.encode()
    if os.environ.get("BIZ_ENV", "dev") != "dev":
        raise RuntimeError("BIZ_FIELD_KEY must be set outside development")
    return base64.urlsafe_b64encode(hashlib.sha256(b"biz360x-dev-field-key").digest())


def encrypt(text: str) -> bytes:
    return Fernet(_key()).encrypt(text.encode())


def decrypt(blob: bytes | memoryview | None) -> str | None:
    return Fernet(_key()).decrypt(bytes(blob)).decode() if blob else None
