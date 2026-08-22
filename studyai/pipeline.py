"""Recoverable full-lecture pipeline with a strict completeness invariant."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from flask import current_app

from .content import ContentGenerationError, generate_full_questions, generate_full_summary
from .jobs import (
    ASSEMBLING,
    COMPLETED,
    DOWNLOADING,
    GENERATING_QUESTIONS,
    PREPARING_MEDIA,
    SEGMENTING,
    SUMMARIZING,
    TRANSCRIBING,
    JobProgress,
    attach_upload,
    complete_segment,
    create_segments,
    fail_job,
    get_job,
    get_segments,
    mark_segment_retry,
    save_job_result,
    set_media_metadata,
    transition_job,
    update_progress,
)
from .services.ai import AIService, AIServiceError
from .services.media import MediaError, MediaService, SegmentFile
from .services.uploads import get_upload, register_downloaded_file, upload_dir
from .services.web_media import WebMediaError, cleanup_download_dir, download_web_media

logger = logging.getLogger(__name__)


class CompletenessError(RuntimeError):
    pass


def process_pipeline(job_id: str, *, sleeper=time.sleep) -> None:
    if get_job(job_id) is None:
        raise LookupError(f"Unknown job {job_id}")
    try:
        segment_files = _prepare_media(job_id, _media_service())
        ai = _ai_service()
        manager = ai if hasattr(ai, "__enter__") else nullcontext(ai)
        with manager as managed_ai:
            _transcribe_all(job_id, segment_files, managed_ai, sleeper)
            transition_job(job_id, ASSEMBLING, JobProgress(ASSEMBLING, 75))
            transcript = assemble_complete_transcript(
                get_segments(job_id), get_job(job_id)["total_segments"]
            )
            save_job_result(job_id, "transcript", transcript)
            transition_job(job_id, SUMMARIZING, JobProgress(SUMMARIZING, 82))
            segment_texts = [row["transcript"] for row in get_segments(job_id)]
            budget = current_app.config["AI_INPUT_TOKEN_BUDGET"]
            save_job_result(
                job_id,
                "summary",
                generate_full_summary(managed_ai, transcript, segment_texts, budget),
            )
            transition_job(
                job_id, GENERATING_QUESTIONS, JobProgress(GENERATING_QUESTIONS, 92)
            )
            save_job_result(
                job_id,
                "questions",
                generate_full_questions(managed_ai, transcript, segment_texts, budget),
            )
            transition_job(job_id, COMPLETED, JobProgress(COMPLETED, 100))
        _cleanup_success(job_id)
        logger.info("lecture_job_completed job_id=%s", job_id)
    except (
        MediaError,
        AIServiceError,
        CompletenessError,
        ContentGenerationError,
        WebMediaError,
        OSError,
    ) as error:
        code = getattr(error, "code", "pipeline_failed")
        public = getattr(error, "public_message", "تعذرت معالجة المحاضرة كاملة.")
        fail_job(job_id, code, public)
        logger.warning("lecture_job_failed job_id=%s category=%s", job_id, code)


def _prepare_media(job_id: str, media: MediaService) -> list[SegmentFile]:
    job = get_job(job_id)
    if not job["upload_id"] and job["source_url"]:
        transition_job(job_id, DOWNLOADING, JobProgress(DOWNLOADING, 2))
        download_root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
        download_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="web-", dir=download_root))
        try:
            path, filename = download_web_media(job["source_url"], temporary)
            upload = register_downloaded_file(job["user_id"], path, filename)
            attach_upload(job_id, upload["id"], filename, upload["total_size"])
            job = get_job(job_id)
        finally:
            cleanup_download_dir(temporary)
    upload = get_upload(job["upload_id"])
    if upload is None or not upload["assembled_path"]:
        raise MediaError("Completed upload is missing")
    if current_app.config["DIRECT_MEDIA_PROCESSING"]:
        return _prepare_direct_media(job_id, upload)
    work_dir = upload_dir(upload["id"]) / "work"
    normalized = work_dir / "normalized.flac"
    segment_dir = work_dir / "segments"
    existing_rows = get_segments(job_id)
    existing_files = [
        segment_dir / f"segment-{row['segment_index']:05d}.flac" for row in existing_rows
    ]
    if existing_rows and all(path.is_file() for path in existing_files):
        return [
            SegmentFile(row["segment_index"], path, row["start_seconds"], row["end_seconds"])
            for row, path in zip(existing_rows, existing_files, strict=True)
        ]
    transition_job(job_id, PREPARING_MEDIA, JobProgress(PREPARING_MEDIA, 5))
    media.check_dependencies()
    info = media.inspect(Path(upload["assembled_path"]))
    set_media_metadata(job_id, info.media_type, info.duration_seconds)
    media.normalize_audio(Path(upload["assembled_path"]), normalized)
    transition_job(job_id, SEGMENTING, JobProgress(SEGMENTING, 12))
    segments = media.segment_audio(
        normalized, segment_dir, info.duration_seconds,
        current_app.config["TRANSCRIPTION_SEGMENT_MINUTES"] * 60,
        current_app.config["TRANSCRIPTION_OVERLAP_SECONDS"],
    )
    create_segments(job_id, segments)
    return segments


def _prepare_direct_media(job_id: str, upload) -> list[SegmentFile]:
    """Send one complete supported media file to Gemini without FFmpeg."""
    source = Path(upload["assembled_path"])
    if not source.is_file() or source.stat().st_size <= 0:
        raise MediaError("Completed upload is missing")
    if get_segments(job_id):
        return [SegmentFile(0, source, 0, 1)]
    transition_job(job_id, PREPARING_MEDIA, JobProgress(PREPARING_MEDIA, 5))
    media_type = "video" if upload["extension"] in {"mp4", "mpeg", "webm"} else "audio"
    set_media_metadata(job_id, media_type, 1)
    transition_job(job_id, SEGMENTING, JobProgress(SEGMENTING, 12))
    segment = SegmentFile(0, source, 0, 1)
    create_segments(job_id, [segment])
    return [segment]


def _transcribe_all(job_id: str, files: list[SegmentFile], ai, sleeper) -> None:
    completed_at_start = sum(row["status"] == "completed" for row in get_segments(job_id))
    transition_job(
        job_id, TRANSCRIBING,
        JobProgress(TRANSCRIBING, 15, completed_at_start, len(files)),
    )
    maximum_attempts = current_app.config["SEGMENT_MAX_RETRIES"]
    for segment_file in files:
        row = get_segments(job_id)[segment_file.index]
        if row["status"] == "completed" and row["transcript"]:
            continue
        while True:
            row = get_segments(job_id)[segment_file.index]
            try:
                complete_segment(
                    job_id, segment_file.index, ai.transcribe_path(segment_file.path)
                )
                completed = sum(
                    item["status"] == "completed" for item in get_segments(job_id)
                )
                update_progress(
                    job_id,
                    JobProgress(
                        TRANSCRIBING, 15 + int(55 * completed / len(files)),
                        completed, len(files),
                    ),
                )
                break
            except AIServiceError as error:
                attempts = row["retry_count"] + 1
                mark_segment_retry(job_id, segment_file.index, error.code)
                if not error.retryable or attempts >= maximum_attempts:
                    raise
                sleeper(min(2 ** (attempts - 1), 30))


def assemble_complete_transcript(rows, expected_total: int | None = None) -> str:
    expected = list(range(len(rows) if expected_total is None else expected_total))
    indexes = [row["segment_index"] for row in rows]
    complete = all(row["status"] == "completed" and row["transcript"] for row in rows)
    if not rows or indexes != expected or not complete:
        raise CompletenessError("Not every expected segment has a successful transcript")
    assembled = rows[0]["transcript"].strip()
    for row in rows[1:]:
        assembled = _merge_overlap(assembled, row["transcript"].strip())
    return assembled


def _merge_overlap(previous: str, current: str, maximum_words: int = 80) -> str:
    previous_words = previous.split()
    current_words = current.split()
    limit = min(maximum_words, len(previous_words), len(current_words))
    for size in range(limit, 2, -1):
        if previous_words[-size:] == current_words[:size]:
            return " ".join(previous_words + current_words[size:])
    return f"{previous}\n\n{current}"


def _media_service():
    factory = current_app.config.get("MEDIA_SERVICE_FACTORY")
    return factory() if factory else MediaService(
        current_app.config["FFMPEG_PATH"], current_app.config["FFPROBE_PATH"]
    )


def _ai_service():
    factory = current_app.config.get("AI_SERVICE_FACTORY")
    return factory() if factory else AIService.from_config(current_app.config)


def _cleanup_success(job_id: str) -> None:
    job = get_job(job_id)
    if job and job["upload_id"]:
        try:
            shutil.rmtree(upload_dir(job["upload_id"]), ignore_errors=False)
        except OSError as error:
            logger.warning(
                "lecture_cleanup_failed job_id=%s category=%s", job_id, type(error).__name__
            )
