"""Authenticated JSON API routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from .auth import login_required
from .services.ai import AIService, AIServiceError

api_bp = Blueprint("api", __name__, url_prefix="/api")
ALLOWED_EXTENSIONS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}


def get_ai_service():
    factory = current_app.config.get("AI_SERVICE_FACTORY")
    return factory() if factory else AIService.from_config(current_app.config)


@api_bp.post("/transcriptions")
@login_required
def transcribe():
    uploaded = request.files.get("audio")
    if uploaded is None or not uploaded.filename:
        return jsonify(error="اختر ملفًا صوتيًا أولًا."), 400
    filename = secure_filename(uploaded.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify(error="صيغة الملف غير مدعومة."), 415
    try:
        text = get_ai_service().transcribe(uploaded.stream, extension)
    except AIServiceError as error:
        current_app.logger.warning("Transcription failed: %s", error)
        return jsonify(error=error.public_message), error.status_code
    return jsonify(message="تم تحويل المحاضرة إلى نص.", filename=filename, text=text), 201


@api_bp.post("/summaries")
@login_required
def summarize():
    return _text_operation("summary")


@api_bp.post("/questions")
@login_required
def questions():
    return _text_operation("questions")


def _text_operation(operation: str):
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    if len(text) < 20:
        return jsonify(error="نص المحاضرة قصير جدًا أو غير موجود."), 400
    if len(text) > 500_000:
        return jsonify(error="نص المحاضرة يتجاوز الحد المسموح."), 413
    try:
        service = get_ai_service()
        result = (
            service.summarize(text)
            if operation == "summary"
            else service.generate_questions(text)
        )
    except AIServiceError as error:
        current_app.logger.warning("AI text operation failed: %s", error)
        return jsonify(error=error.public_message), error.status_code
    return jsonify(result=result)
