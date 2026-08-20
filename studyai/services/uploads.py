"""Resumable upload storage with bounded streaming and deterministic assembly."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import uuid
from pathlib import Path

from flask import current_app

from ..db import get_db

ALLOWED_EXTENSIONS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}


class UploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def create_upload(user_id: int, filename: str, total_size: int, requested_chunk_size: int | None):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadError("صيغة الملف غير مدعومة.", 415)
    maximum = current_app.config["MAX_UPLOAD_SIZE_BYTES"]
    if total_size <= 0:
        raise UploadError("الملف فارغ.")
    if total_size > maximum:
        raise UploadError("حجم الملف يتجاوز الحد المسموح.", 413)
    configured_chunk = current_app.config["UPLOAD_CHUNK_SIZE_BYTES"]
    chunk_size = requested_chunk_size or configured_chunk
    if chunk_size <= 0 or chunk_size > configured_chunk:
        raise UploadError("حجم الجزء غير صالح.")
    expected_chunks = math.ceil(total_size / chunk_size)
    if expected_chunks > math.ceil(maximum / min(chunk_size, configured_chunk)) + 1:
        raise UploadError("عدد الأجزاء يتجاوز الحد المسموح.")
    _ensure_disk_space(total_size)
    upload_id = uuid.uuid4().hex
    upload_dir(upload_id).mkdir(parents=True, exist_ok=False)
    database = get_db()
    database.execute(
        """INSERT INTO upload_sessions
           (id, user_id, original_filename, extension, total_size, chunk_size,
            expected_chunks, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'uploading')""",
        (upload_id, user_id, filename[:255], extension, total_size, chunk_size, expected_chunks),
    )
    database.commit()
    return get_upload(upload_id, user_id)


def get_upload(upload_id: str, user_id: int | None = None):
    query = "SELECT * FROM upload_sessions WHERE id = ?"
    parameters: tuple[object, ...] = (upload_id,)
    if user_id is not None:
        query += " AND user_id = ?"
        parameters += (user_id,)
    return get_db().execute(query, parameters).fetchone()


def save_chunk(upload_id: str, user_id: int, index: int, stream):
    upload = get_upload(upload_id, user_id)
    if upload is None:
        raise UploadError("جلسة الرفع غير موجودة.", 404)
    if upload["status"] != "uploading":
        raise UploadError("جلسة الرفع لم تعد تستقبل أجزاء.", 409)
    if index < 0 or index >= upload["expected_chunks"]:
        raise UploadError("رقم الجزء غير صالح.")
    expected_size = _expected_chunk_size(upload, index)
    destination = chunk_path(upload_id, index)
    temporary = destination.with_suffix(".part")
    digest = hashlib.sha256()
    written = 0
    try:
        with temporary.open("wb") as output:
            while data := stream.read(1024 * 1024):
                written += len(data)
                if written > expected_size:
                    raise UploadError("حجم الجزء أكبر من المتوقع.", 413)
                digest.update(data)
                output.write(data)
        if written != expected_size:
            raise UploadError("حجم الجزء لا يطابق الحجم المتوقع.")
        checksum = digest.hexdigest()
        existing = get_db().execute(
            "SELECT size, sha256 FROM upload_chunks WHERE upload_id = ? AND chunk_index = ?",
            (upload_id, index),
        ).fetchone()
        if existing:
            if existing["size"] != written or existing["sha256"] != checksum:
                raise UploadError("تم رفع جزء مختلف بهذا الرقم مسبقًا.", 409)
            temporary.unlink(missing_ok=True)
            return existing
        os.replace(temporary, destination)
        database = get_db()
        database.execute(
            "INSERT INTO upload_chunks (upload_id, chunk_index, size, sha256) VALUES (?, ?, ?, ?)",
            (upload_id, index, written, checksum),
        )
        database.execute(
            """UPDATE upload_sessions SET received_chunks = received_chunks + 1,
               updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (upload_id,),
        )
        database.commit()
        return database.execute(
            "SELECT size, sha256 FROM upload_chunks WHERE upload_id = ? AND chunk_index = ?",
            (upload_id, index),
        ).fetchone()
    finally:
        temporary.unlink(missing_ok=True)


def complete_upload(upload_id: str, user_id: int) -> Path:
    upload = get_upload(upload_id, user_id)
    if upload is None:
        raise UploadError("جلسة الرفع غير موجودة.", 404)
    if upload["status"] == "completed" and upload["assembled_path"]:
        return Path(upload["assembled_path"])
    rows = get_db().execute(
        "SELECT chunk_index, size FROM upload_chunks WHERE upload_id = ? ORDER BY chunk_index",
        (upload_id,),
    ).fetchall()
    indexes = [row["chunk_index"] for row in rows]
    if indexes != list(range(upload["expected_chunks"])):
        raise UploadError("لم تصل جميع أجزاء الملف بعد.", 409)
    if sum(row["size"] for row in rows) != upload["total_size"]:
        raise UploadError("الحجم النهائي لا يطابق الحجم المتوقع.", 409)
    _ensure_disk_space(upload["total_size"])
    assembled = upload_dir(upload_id) / f"source.{upload['extension']}"
    temporary = assembled.with_suffix(f".{upload['extension']}.part")
    try:
        with temporary.open("wb") as output:
            for index in indexes:
                with chunk_path(upload_id, index).open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        if temporary.stat().st_size != upload["total_size"]:
            raise UploadError("فشل التحقق من الملف المجمّع.", 500)
        os.replace(temporary, assembled)
        database = get_db()
        database.execute(
            """UPDATE upload_sessions SET status = 'completed', assembled_path = ?,
               completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (str(assembled), upload_id),
        )
        for index in indexes:
            chunk_path(upload_id, index).unlink(missing_ok=True)
        database.execute("DELETE FROM upload_chunks WHERE upload_id = ?", (upload_id,))
        database.commit()
        return assembled
    finally:
        temporary.unlink(missing_ok=True)


def cleanup_stale_uploads(max_age_hours: int = 24) -> int:
    database = get_db()
    stale = database.execute(
        """SELECT id FROM upload_sessions
           WHERE status = 'uploading' AND updated_at < datetime('now', ?)""",
        (f"-{max_age_hours} hours",),
    ).fetchall()
    for row in stale:
        shutil.rmtree(upload_dir(row["id"]), ignore_errors=False)
        database.execute("DELETE FROM upload_sessions WHERE id = ?", (row["id"],))
    database.commit()
    return len(stale)


def upload_dir(upload_id: str) -> Path:
    if len(upload_id) != 32 or not all(character in "0123456789abcdef" for character in upload_id):
        raise UploadError("معرّف الرفع غير صالح.")
    return Path(current_app.config["UPLOAD_ROOT"]).resolve() / upload_id


def chunk_path(upload_id: str, index: int) -> Path:
    return upload_dir(upload_id) / f"chunk-{index:08d}"


def _expected_chunk_size(upload, index: int) -> int:
    if index < upload["expected_chunks"] - 1:
        return upload["chunk_size"]
    return upload["total_size"] - upload["chunk_size"] * (upload["expected_chunks"] - 1)


def _ensure_disk_space(required_bytes: int) -> None:
    root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    reserve = current_app.config["MIN_FREE_DISK_MB"] * 1024**2
    if shutil.disk_usage(root).free < required_bytes + reserve:
        raise UploadError("لا توجد مساحة تخزين كافية لإكمال الرفع.", 507)
