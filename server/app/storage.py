"""Object storage for the document vault.

`LocalStore` keeps bytes on disk under BIZ_STORAGE_DIR (development, tests, single-node). The production store is an
S3-compatible bucket in an Indian region with encryption at rest; it implements the same three methods and signs its
own URLs. Upload and download URLs here are capability tokens: a short-lived JWT naming the operation, the object
key and (for uploads) the expected size and SHA-256, so the bytes are checked before they are accepted.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import pathlib
import time

import jwt

from .core import JWT_SECRET

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
MAX_BYTES = int(os.environ.get("BIZ_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
UPLOAD_TTL = 15 * 60
DOWNLOAD_TTL = 5 * 60
TEXT_TYPES = ("text/plain", "text/markdown", "text/csv", "application/json")


def root() -> pathlib.Path:
    p = pathlib.Path(os.environ.get("BIZ_STORAGE_DIR") or pathlib.Path(__file__).resolve().parents[2] / "var" / "storage")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(key: str) -> pathlib.Path:
    p = (root() / key).resolve()
    if root().resolve() not in p.parents:
        raise ValueError("storage key escapes the storage root")
    return p


def put(key: str, data: bytes) -> None:
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def get(key: str) -> bytes | None:
    p = _path(key)
    return p.read_bytes() if p.is_file() else None


def storage_key(entity_id, sha256: str) -> str:
    return f"{entity_id}/{sha256[:2]}/{sha256}"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign(op: str, key: str, ttl: int, **claims) -> str:
    return jwt.encode({"op": op, "key": key, "exp": int(time.time()) + ttl, **claims}, JWT_SECRET, algorithm="HS256")


def verify(token: str, op: str) -> dict:
    claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    if claims.get("op") != op:
        raise jwt.InvalidTokenError("wrong operation")
    return claims


def upload_ticket(key: str, document_id, sha256: str, size: int, mime: str) -> dict:
    token = sign("put", key, UPLOAD_TTL, doc=str(document_id), sha256=sha256, size=size)
    return {"upload_url": f"{API_BASE_URL}/v1/storage/{token}", "upload_method": "PUT", "upload_headers": {"Content-Type": mime},
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=UPLOAD_TTL)).isoformat()}


def download_url(key: str, file_name: str, mime: str) -> str:
    return f"{API_BASE_URL}/v1/storage/{sign('get', key, DOWNLOAD_TTL, name=file_name, mime=mime)}"


def text_of(data: bytes, mime: str) -> str | None:
    """The text layer of a plain-text document. PDFs and images need OCR, which is a worker concern (M6/M9)."""
    if mime.split(";")[0].strip().lower() in TEXT_TYPES:
        return data.decode("utf-8", errors="replace").replace("\x00", "")[:500_000]
    return None
