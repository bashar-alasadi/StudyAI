"""Resumable upload and processing-job API."""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from .auth import login_required
from .jobs import QUEUED, UPLOADED, create_job, fail_job, get_job_for_upload, transition_job
from .queueing import get_job_queue
from .services.uploads import UploadError, complete_upload, create_upload, get_upload, save_chunk
from .services.web_media import WebMediaError, validate_source_url

uploads_bp = Blueprint("uploads", __name__, url_prefix="/api/uploads")


@uploads_bp.post("/url")
@login_required
def initialize_url_upload():
    payload = request.get_json(silent=True) or {}
    try:
        source_url = validate_source_url(str(payload.get("url", "")))
    except WebMediaError as error:
        return jsonify(error=error.public_message), 400
    job_id = create_job(
        g.user["id"], "رابط محاضرة", status=QUEUED, source_url=source_url
    )
    try:
        get_job_queue().enqueue(job_id)
    except Exception as error:
        current_app.logger.warning(
            "url_queue_enqueue_failed job_id=%s category=%s", job_id, type(error).__name__
        )
        fail_job(job_id, "queue_unavailable", "تعذر إرسال المهمة إلى عامل المعالجة.")
        return jsonify(error="خدمة المعالجة غير متاحة مؤقتًا.", job_id=job_id), 503
    return jsonify(job_id=job_id, status=QUEUED), 202


@uploads_bp.post("")
@login_required
def initialize_upload():
    payload = request.get_json(silent=True) or {}
    try:
        upload = create_upload(
            g.user["id"],
            str(payload.get("filename", "")),
            int(payload.get("total_size", 0)),
            int(payload["chunk_size"]) if payload.get("chunk_size") else None,
        )
    except (UploadError, TypeError, ValueError) as error:
        status = error.status_code if isinstance(error, UploadError) else 400
        message = str(error) if isinstance(error, UploadError) else "بيانات الرفع غير صالحة."
        return jsonify(error=message), status
    return jsonify(_serialize_upload(upload)), 201


@uploads_bp.get("/<upload_id>")
@login_required
def upload_status(upload_id: str):
    upload = get_upload(upload_id, g.user["id"])
    if upload is None:
        return jsonify(error="جلسة الرفع غير موجودة."), 404
    return jsonify(_serialize_upload(upload))


@uploads_bp.put("/<upload_id>/chunks/<int:index>")
@login_required
def upload_chunk(upload_id: str, index: int):
    try:
        chunk = save_chunk(upload_id, g.user["id"], index, request.stream)
    except UploadError as error:
        return jsonify(error=str(error)), error.status_code
    return jsonify(index=index, size=chunk["size"])


@uploads_bp.post("/<upload_id>/complete")
@login_required
def finalize_upload(upload_id: str):
    try:
        complete_upload(upload_id, g.user["id"])
        upload = get_upload(upload_id, g.user["id"])
    except UploadError as error:
        return jsonify(error=str(error)), error.status_code
    existing_job = get_job_for_upload(upload_id, g.user["id"])
    if existing_job:
        return jsonify(job_id=existing_job["id"], status=existing_job["status"]), 202
    job_id = create_job(
        g.user["id"], upload["original_filename"], upload["total_size"], UPLOADED, upload_id
    )
    transition_job(job_id, QUEUED)
    try:
        get_job_queue().enqueue(job_id)
    except Exception as error:
        current_app.logger.warning(
            "queue_enqueue_failed job_id=%s category=%s", job_id, type(error).__name__
        )
        fail_job(job_id, "queue_unavailable", "تعذر إرسال المهمة إلى عامل المعالجة.")
        return jsonify(error="خدمة المعالجة غير متاحة مؤقتًا.", job_id=job_id), 503
    return jsonify(job_id=job_id, status=QUEUED), 202


def _serialize_upload(upload) -> dict[str, object]:
    return {
        "upload_id": upload["id"],
        "status": upload["status"],
        "total_size": upload["total_size"],
        "chunk_size": upload["chunk_size"],
        "expected_chunks": upload["expected_chunks"],
        "received_chunks": upload["received_chunks"],
    }
