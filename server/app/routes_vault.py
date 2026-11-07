"""Document vault: registration and signed upload/download, search, expiry, advisor requests, data rooms."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

import jwt
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from . import storage
from .core import ApiError, Auth, Principal, audit, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
FOLDERS = ("incorporation", "tax", "mca", "payroll", "contracts", "board", "bank_finance", "sales", "purchases", "licences", "other")
ROLES = ("attachment", "acknowledgement", "signed_copy", "draft", "evidence", "certificate")


# ================================================================== shared helpers (also used by legal, notices, jobs)
def link_document(cur, document_id, subject: str, subject_id, role: str = "attachment") -> None:
    cur.execute("INSERT INTO document_links (document_id, subject, subject_id, role) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (document_id, subject, subject_id, role))


def store_document(cur, entity_id, *, folder: str, title: str, file_name: str, mime: str, data: bytes, source: str = "biz360x",
                   links: list[tuple[str, uuid.UUID, str]] | None = None, period_label: str | None = None, valid_until: dt.date | None = None) -> uuid.UUID:
    """Create a document from bytes the server itself produced (a draft letter, a generated file). Deduplicates on content."""
    sha = storage.sha256_of(data)
    key = storage.storage_key(entity_id, sha)
    cur.execute("SELECT id FROM documents WHERE entity_id = %s AND sha256 = %s", (entity_id, sha))
    existing = cur.fetchone()
    if existing:
        doc_id = existing["id"]
    else:
        storage.put(key, data)
        cur.execute("""INSERT INTO documents (entity_id, folder, title, file_name, mime_type, size_bytes, sha256, storage_key, source, period_label, valid_until,
                              extracted_text, uploaded_by, uploaded_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::doc_source, %s, %s, %s, app.current_user_id(), now())
                       RETURNING id""", (entity_id, folder, title, file_name, mime, len(data), sha, key, source, period_label, valid_until, storage.text_of(data, mime)))
        doc_id = cur.fetchone()["id"]
        audit(cur, entity_id, "document.stored", "documents", doc_id, after={"title": title, "source": source}, actor_kind="system" if source == "biz360x" else "user")
    for subject, subject_id, role in links or []:
        link_document(cur, doc_id, subject, subject_id, role)
    return doc_id


def load_document(cur, entity_id, did) -> dict:
    cur.execute("""SELECT id, folder, title, file_name, mime_type, size_bytes, trim(sha256) AS sha256, source::text, period_label, valid_until, created_at,
                          storage_key, uploaded_at FROM documents WHERE id = %s AND entity_id = %s""", (did, entity_id))
    d = cur.fetchone()
    if not d:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT subject, subject_id, role FROM document_links WHERE document_id = %s ORDER BY subject, role", (did,))
    d["links"] = cur.fetchall()
    d["download_url"] = storage.download_url(d["storage_key"], d["file_name"], d["mime_type"]) if d.pop("uploaded_at") else None
    d.pop("storage_key")
    return js(d)


# ================================================================== documents
class LinkIn(BaseModel):
    subject: str = Field(min_length=1, max_length=40)
    subject_id: uuid.UUID
    role: Literal["attachment", "acknowledgement", "signed_copy", "draft", "evidence", "certificate"] = "attachment"


class DocumentIn(BaseModel):
    folder: Literal["incorporation", "tax", "mca", "payroll", "contracts", "board", "bank_finance", "sales", "purchases", "licences", "other"]
    title: str = Field(min_length=1, max_length=200)
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    period_label: str | None = None
    valid_until: dt.date | None = None
    links: list[LinkIn] = []


@router.post(E + "/documents", status_code=201, tags=["Documents"])
def create_document(entity_id: uuid.UUID, body: DocumentIn, response: Response, p: Principal = Auth):
    if body.size_bytes > storage.MAX_BYTES:
        raise ApiError(400, "validation_failed", f"Files are limited to {storage.MAX_BYTES // (1024 * 1024)} MB")
    sha = body.sha256.lower()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        cur.execute("SELECT id, uploaded_at, storage_key FROM documents WHERE entity_id = %s AND sha256 = %s", (entity_id, sha))
        dup = cur.fetchone()
        if dup:                                                          # same bytes already registered: return that document
            for l in body.links:
                link_document(cur, dup["id"], l.subject, l.subject_id, l.role)
            doc = load_document(cur, entity_id, dup["id"])
            ticket = None if dup["uploaded_at"] else storage.upload_ticket(dup["storage_key"], dup["id"], sha, body.size_bytes, body.mime_type)
            response.status_code = 200
            return {"document": doc, **(ticket or {"upload_url": None, "upload_method": "PUT", "upload_headers": {}, "expires_at": None})}
        key = storage.storage_key(entity_id, sha)
        cur.execute("""INSERT INTO documents (entity_id, folder, title, file_name, mime_type, size_bytes, sha256, storage_key, source, period_label, valid_until, uploaded_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'upload', %s, %s, app.current_user_id()) RETURNING id""",
                    (entity_id, body.folder, body.title, body.file_name, body.mime_type, body.size_bytes, sha, key, body.period_label, body.valid_until))
        did = cur.fetchone()["id"]
        for l in body.links:
            link_document(cur, did, l.subject, l.subject_id, l.role)
        audit(cur, entity_id, "document.registered", "documents", did, after={"title": body.title, "size": body.size_bytes})
        return {"document": load_document(cur, entity_id, did), **storage.upload_ticket(key, did, sha, body.size_bytes, body.mime_type)}


@router.get(E + "/documents", tags=["Documents"])
def search_documents(entity_id: uuid.UUID, p: Principal = Auth, q: str | None = None, folder: str | None = None, source: str | None = None,
                     expiring_within_days: int | None = Query(default=None, ge=0, le=3650), subject: str | None = None, subject_id: uuid.UUID | None = None,
                     limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:read")
        cur.execute("""SELECT d.id FROM documents d
                        WHERE d.entity_id = %s AND (%s::text IS NULL OR d.search @@ websearch_to_tsquery('simple', %s)) AND (%s::text IS NULL OR d.folder = %s)
                          AND (%s::text IS NULL OR d.source::text = %s)
                          AND (%s::int IS NULL OR d.valid_until BETWEEN current_date AND current_date + %s::int)
                          AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM document_links l WHERE l.document_id = d.id AND l.subject = %s
                                                            AND (%s::uuid IS NULL OR l.subject_id = %s)))
                        ORDER BY CASE WHEN %s::text IS NULL THEN 0 ELSE -ts_rank(d.search, websearch_to_tsquery('simple', %s)) END,
                                 CASE WHEN %s::int IS NULL THEN NULL ELSE d.valid_until END, d.created_at DESC, d.id LIMIT %s OFFSET %s""",
                    (entity_id, q, q, folder, folder, source, source, expiring_within_days, expiring_within_days, subject, subject, subject_id, subject_id,
                     q, q, expiring_within_days, limit + 1, off))
        return page([load_document(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/documents/{document_id}", tags=["Documents"])
def get_document(entity_id: uuid.UUID, document_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:read")
        return load_document(cur, entity_id, document_id)


@router.patch(E + "/documents/{document_id}", tags=["Documents"])
def update_document(entity_id: uuid.UUID, document_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        cur.execute("SELECT folder, title, period_label, valid_until FROM documents WHERE id = %s AND entity_id = %s FOR UPDATE", (document_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        new = {k: body.get(k, d[k]) for k in ("folder", "title", "period_label", "valid_until")}
        if new["folder"] not in FOLDERS or not str(new["title"]).strip():
            raise ApiError(400, "validation_failed", "Unknown folder or empty title")
        try:
            new["valid_until"] = dt.date.fromisoformat(new["valid_until"]) if isinstance(new["valid_until"], str) else new["valid_until"]
        except ValueError:
            raise ApiError(400, "validation_failed", "valid_until must be YYYY-MM-DD")
        cur.execute("UPDATE documents SET folder = %s, title = %s, period_label = %s, valid_until = %s WHERE id = %s",
                    (new["folder"], new["title"], new["period_label"], new["valid_until"], document_id))
        for l in body.get("links", []):
            try:
                li = LinkIn(**l)
            except Exception as e:
                raise ApiError(400, "validation_failed", "Invalid link", str(e))
            link_document(cur, document_id, li.subject, li.subject_id, li.role)
        audit(cur, entity_id, "document.updated", "documents", document_id, after={k: v for k, v in body.items() if k != "links"})
        return load_document(cur, entity_id, document_id)


# ================================================================== signed upload / download (the token is the credential)
@router.put("/storage/{token}", status_code=204, tags=["Documents"])
async def upload_bytes(token: str, request: Request):
    try:
        c = storage.verify(token, "put")
    except jwt.PyJWTError:
        raise ApiError(403, "permission_denied", "The upload link is invalid or has expired")
    data = await request.body()
    if len(data) != c["size"] or storage.sha256_of(data) != c["sha256"]:
        raise ApiError(400, "validation_failed", "The uploaded bytes do not match the registered size and SHA-256")
    with tx(None, role="biz_worker") as cur:
        cur.execute("SELECT id, entity_id, mime_type, uploaded_at FROM documents WHERE id = %s AND storage_key = %s FOR UPDATE", (c["doc"], c["key"]))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        if d["uploaded_at"]:
            return None                                                  # idempotent: the same bytes were already accepted
        storage.put(c["key"], data)
        cur.execute("UPDATE documents SET uploaded_at = now(), extracted_text = %s WHERE id = %s", (storage.text_of(data, d["mime_type"]), d["id"]))
        audit(cur, d["entity_id"], "document.uploaded", "documents", d["id"], after={"size": len(data)}, actor_kind="integration")


@router.get("/storage/{token}", tags=["Documents"])
def download_bytes(token: str):
    try:
        c = storage.verify(token, "get")
    except jwt.PyJWTError:
        raise ApiError(403, "permission_denied", "The download link is invalid or has expired")
    try:
        data = storage.get(c["key"])
    except ValueError:
        data = None
    if data is None:
        raise ApiError(404, "not_found", "Not found")
    safe = c["name"].replace('"', "")
    return Response(data, media_type=c["mime"], headers={"Content-Disposition": f'attachment; filename="{safe}"'})


# ================================================================== advisor document requests
class RequestIn(BaseModel):
    advisor_grant_id: uuid.UUID | None = None
    requested_by_name: str | None = None
    title: str = Field(min_length=1, max_length=200)
    due_on: dt.date | None = None


def load_request(cur, entity_id, rid) -> dict:
    cur.execute("SELECT id, advisor_grant_id, requested_by_name, title, due_on, status FROM document_requests WHERE id = %s AND entity_id = %s", (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT document_id FROM document_request_items WHERE request_id = %s ORDER BY shared_at, document_id", (rid,))
    r["document_ids"] = [x["document_id"] for x in cur.fetchall()]
    return js(r)


@router.post(E + "/document-requests", status_code=201, tags=["Documents"])
def create_request(entity_id: uuid.UUID, body: RequestIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        name = body.requested_by_name
        if not name:
            cur.execute("SELECT full_name FROM users WHERE id = app.current_user_id()")
            name = cur.fetchone()["full_name"]
        cur.execute("INSERT INTO document_requests (entity_id, advisor_grant_id, requested_by_name, title, due_on) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (entity_id, body.advisor_grant_id, name, body.title, body.due_on))
        rid = cur.fetchone()["id"]
        audit(cur, entity_id, "document_request.created", "document_requests", rid, after={"title": body.title})
        return load_request(cur, entity_id, rid)


@router.get(E + "/document-requests", tags=["Documents"])
def list_requests(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:read")
        cur.execute("SELECT id FROM document_requests WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY due_on NULLS LAST, created_at DESC LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page([load_request(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


class ShareIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1)


@router.post(E + "/document-requests/{request_id}/share", tags=["Documents"])
def share_documents(entity_id: uuid.UUID, request_id: uuid.UUID, body: ShareIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        cur.execute("SELECT status FROM document_requests WHERE id = %s AND entity_id = %s FOR UPDATE", (request_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        if r["status"] in ("fulfilled", "cancelled"):
            raise ApiError(409, "conflict", f"Request is already {r['status']}")
        for did in body.document_ids:
            cur.execute("SELECT 1 FROM documents WHERE id = %s AND entity_id = %s AND uploaded_at IS NOT NULL", (did, entity_id))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", f"Document {did} does not exist or has no file yet")
            cur.execute("INSERT INTO document_request_items (request_id, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (request_id, did))
        cur.execute("UPDATE document_requests SET status = 'fulfilled' WHERE id = %s", (request_id,))
        audit(cur, entity_id, "document_request.fulfilled", "document_requests", request_id, after={"documents": len(body.document_ids)})
        return load_request(cur, entity_id, request_id)


# ================================================================== data rooms
class AccessIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class DataRoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: Literal["fundraise", "lender", "audit", "acquisition", "other"] = "other"
    expires_at: dt.datetime | None = None
    watermark: bool = True
    allow_download: bool = False
    document_ids: list[uuid.UUID] = []
    access: list[AccessIn] = []


def load_room(cur, entity_id, rid) -> dict:
    cur.execute("SELECT id, name, purpose, expires_at, watermark, allow_download FROM data_rooms WHERE id = %s AND entity_id = %s", (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT document_id FROM data_room_items WHERE room_id = %s ORDER BY document_id", (rid,))
    r["document_ids"] = [x["document_id"] for x in cur.fetchall()]
    cur.execute("SELECT email, last_viewed_at FROM data_room_access WHERE room_id = %s AND revoked_at IS NULL ORDER BY email", (rid,))
    r["access"] = cur.fetchall()
    return js(r)


def _write_room(cur, entity_id, rid, body: DataRoomIn) -> None:
    for did in body.document_ids:
        cur.execute("SELECT 1 FROM documents WHERE id = %s AND entity_id = %s", (did, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", f"Document {did} does not exist")
    cur.execute("DELETE FROM data_room_items WHERE room_id = %s", (rid,))
    for did in body.document_ids:
        cur.execute("INSERT INTO data_room_items (room_id, document_id) VALUES (%s, %s)", (rid, did))
    emails = {a.email.lower() for a in body.access}
    cur.execute("UPDATE data_room_access SET revoked_at = now() WHERE room_id = %s AND revoked_at IS NULL AND NOT (email::text = ANY(%s))", (rid, list(emails)))
    for e in emails:
        cur.execute("""INSERT INTO data_room_access (room_id, email) VALUES (%s, %s) ON CONFLICT (room_id, email) DO UPDATE SET revoked_at = NULL, granted_at = now()
                        WHERE data_room_access.revoked_at IS NOT NULL""", (rid, e))


@router.post(E + "/data-rooms", status_code=201, tags=["Documents"])
def create_room(entity_id: uuid.UUID, body: DataRoomIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        cur.execute("INSERT INTO data_rooms (entity_id, name, purpose, expires_at, watermark, allow_download) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (entity_id, body.name, body.purpose, body.expires_at, body.watermark, body.allow_download))
        rid = cur.fetchone()["id"]
        _write_room(cur, entity_id, rid, body)
        audit(cur, entity_id, "data_room.created", "data_rooms", rid, after={"name": body.name, "documents": len(body.document_ids), "viewers": len(body.access)})
        return load_room(cur, entity_id, rid)


@router.get(E + "/data-rooms", tags=["Documents"])
def list_rooms(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:read")
        cur.execute("SELECT id FROM data_rooms WHERE entity_id = %s ORDER BY name LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([load_room(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/data-rooms/{room_id}", tags=["Documents"])
def get_room(entity_id: uuid.UUID, room_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:read")
        return load_room(cur, entity_id, room_id)


@router.patch(E + "/data-rooms/{room_id}", tags=["Documents"])
def update_room(entity_id: uuid.UUID, room_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        cur_room = load_room(cur, entity_id, room_id)
        merged = {**cur_room, **{k: v for k, v in body.items() if k in ("name", "purpose", "expires_at", "watermark", "allow_download", "document_ids", "access")}}
        try:
            new = DataRoomIn(**{k: merged[k] for k in ("name", "purpose", "expires_at", "watermark", "allow_download", "document_ids")},
                             access=[AccessIn(email=a["email"]) for a in merged["access"]])
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid data room", str(e))
        cur.execute("UPDATE data_rooms SET name = %s, purpose = %s, expires_at = %s, watermark = %s, allow_download = %s WHERE id = %s",
                    (new.name, new.purpose, new.expires_at, new.watermark, new.allow_download, room_id))
        _write_room(cur, entity_id, room_id, new)
        audit(cur, entity_id, "data_room.updated", "data_rooms", room_id)
        return load_room(cur, entity_id, room_id)


@router.delete(E + "/data-rooms/{room_id}", status_code=204, tags=["Documents"])
def delete_room(entity_id: uuid.UUID, room_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "documents:write")
        cur.execute("DELETE FROM data_rooms WHERE id = %s AND entity_id = %s", (room_id, entity_id))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Not found")
        audit(cur, entity_id, "data_room.deleted", "data_rooms", room_id)
