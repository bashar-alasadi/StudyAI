"""Owner-scoped processing job status, result, and retry API."""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, current_app, jsonify, send_file, session

from .auth import owns_resource, public_user_id
from .jobs import FAILED, fail_job, get_job, latest_job, prepare_retry
from .queueing import get_job_queue
from .services.exports import (
    build_docx,
    build_markdown,
    build_pdf,
    format_organized_transcript,
    organize_transcript,
)

api_bp = Blueprint("api", __name__, url_prefix="/api/jobs")


@api_bp.get("/latest")
def latest():
    if session.get("admin_authenticated"):
        job = latest_job(public_user_id())
    else:
        job_ids = session.get("public_job_ids", [])
        job = get_job(job_ids[-1], public_user_id()) if job_ids else None
    return (jsonify(job=None), 200) if job is None else jsonify(_serialize_status(job))


@api_bp.get("/<job_id>")
def status(job_id: str):
    if not owns_resource("job", job_id):
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    job = get_job(job_id, public_user_id())
    if job is None:
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    if job["status"] in {"uploaded", "queued"}:
        try:
            get_job_queue().enqueue(job_id)
        except Exception:
            current_app.logger.exception("Could not recover queued job %s", job_id)
    return jsonify(_serialize_status(job))


@api_bp.get("/<job_id>/result")
def result(job_id: str):
    if not owns_resource("job", job_id):
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    job = get_job(job_id, public_user_id())
    if job is None:
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    if job["status"] != "completed":
        return jsonify(error="نتيجة المحاضرة لم تكتمل بعد."), 409
    transcript = job["transcript"] or ""
    return jsonify(
        job_id=job["id"],
        transcript=transcript,
        transcript_sections=[
            {"title": section.title, "text": section.text}
            for section in organize_transcript(transcript)
        ],
        summary=job["summary"],
        questions=job["questions"],
        explanation=job["explanation"],
    )


@api_bp.get("/<job_id>/export/<result_type>.<file_format>")
def export_result(job_id: str, result_type: str, file_format: str):
    if not owns_resource("job", job_id):
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    job = get_job(job_id, public_user_id())
    if job is None:
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    if job["status"] != "completed":
        return jsonify(error="نتيجة المحاضرة لم تكتمل بعد."), 409
    result_types = {
        "transcript": ("transcript", "التفريغ الكامل"),
        "summary": ("summary", "ملخص المحاضرة"),
        "questions": ("questions", "أسئلة المراجعة وإجاباتها"),
        "explanation": ("explanation", "الشرح والأمثلة التوضيحية"),
    }
    if result_type not in result_types:
        return jsonify(error="نوع النتيجة المطلوب تصديرها غير مدعوم."), 404
    field, title = result_types[result_type]
    content_text = (job[field] or "").strip()
    if not content_text:
        return jsonify(error="لا يوجد محتوى لتصديره في هذا القسم."), 409
    if result_type == "transcript":
        content_text = format_organized_transcript(content_text)

    exporters = {
        "md": (build_markdown, "text/markdown; charset=utf-8"),
        "docx": (
            build_docx,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "pdf": (build_pdf, "application/pdf"),
    }
    if file_format not in exporters:
        return jsonify(error="صيغة التصدير غير مدعومة."), 404
    builder, mime_type = exporters[file_format]
    payload = builder(content_text, job["original_filename"], title)
    response = send_file(
        BytesIO(payload),
        mimetype=mime_type,
        as_attachment=True,
        download_name=f"studyai-{result_type}-{job_id[:8]}.{file_format}",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@api_bp.post("/<job_id>/retry")
def retry(job_id: str):
    if not owns_resource("job", job_id):
        return jsonify(error="مهمة المعالجة غير موجودة."), 404
    job = get_job(job_id, public_user_id())
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
