"""Owner-scoped processing job status, result, and retry API."""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify

from .auth import login_required
from .jobs import FAILED, fail_job, get_job, latest_job, prepare_retry
from .queueing import get_job_queue

api_bp = Blueprint("api", __name__, url_prefix="/api/jobs")


@api_bp.get("/latest")
@login_required
def latest():
    job = latest_job(g.user["id"])
    return (jsonify(job=None), 200) if job is None else jsonify(_serialize_status(job))


@api_bp.get("/<job_id>")
@login_required
def status(job_id: str):
    job = get_job(job_id, g.user["id"])
    if job is None:
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    return jsonify(_serialize_status(job))


@api_bp.get("/<job_id>/result")
@login_required
def result(job_id: str):
    job = get_job(job_id, g.user["id"])
    if job is None:
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    if job["status"] != "completed":
        return jsonify(error="نتيجة المحاضرة لم تكتمل بعد."), 409
    return jsonify(
        job_id=job["id"],
        transcript=job["transcript"],
        summary=job["summary"],
        questions=job["questions"],
    )


@api_bp.post("/<job_id>/retry")
@login_required
def retry(job_id: str):
    job = get_job(job_id, g.user["id"])
    if job is None:
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    if job["status"] != FAILED:
        return jsonify(error="يمكن إعادة محاولة المهام الفاشلة فقط."), 409
    prepare_retry(job_id)
    try:
        get_job_queue().enqueue(job_id)
    except Exception as error:
        current_app.logger.warning(
            "queue_retry_failed job_id=%s category=%s", job_id, type(error).__name__
        )
        fail_job(job_id, "queue_unavailable", "تعذر إرسال المهمة إلى عامل المعالجة.")
        return jsonify(error="خدمة المعالجة غير متاحة مؤقتًا."), 503
    return jsonify(job_id=job_id, status="queued"), 202


def _serialize_status(job) -> dict[str, object]:
    return {
        "job_id": job["id"],
        "status": job["status"],
        "stage": job["current_stage"],
        "progress": job["progress"],
        "completed_segments": job["completed_segments"],
        "total_segments": job["total_segments"],
        "filename": job["original_filename"],
        "error": job["safe_error_message"],
        "created_at": job["created_at"],
    }
