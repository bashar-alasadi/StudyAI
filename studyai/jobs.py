"""Persistent processing jobs and their explicit state machine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from .db import get_db

UPLOADING = "uploading"
UPLOADED = "uploaded"
QUEUED = "queued"
PREPARING_MEDIA = "preparing_media"
SEGMENTING = "segmenting"
TRANSCRIBING = "transcribing"
ASSEMBLING = "assembling"
SUMMARIZING = "summarizing"
GENERATING_QUESTIONS = "generating_questions"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATES = frozenset({COMPLETED, FAILED, CANCELLED})
TRANSITIONS = {
    UPLOADING: {UPLOADED, FAILED, CANCELLED},
    UPLOADED: {QUEUED, FAILED, CANCELLED},
    QUEUED: {PREPARING_MEDIA, TRANSCRIBING, FAILED, CANCELLED},
    PREPARING_MEDIA: {SEGMENTING, FAILED, CANCELLED},
    SEGMENTING: {TRANSCRIBING, FAILED, CANCELLED},
    TRANSCRIBING: {ASSEMBLING, FAILED, CANCELLED},
    ASSEMBLING: {SUMMARIZING, FAILED, CANCELLED},
    SUMMARIZING: {GENERATING_QUESTIONS, FAILED, CANCELLED},
    GENERATING_QUESTIONS: {COMPLETED, FAILED, CANCELLED},
    FAILED: {QUEUED, CANCELLED},
    COMPLETED: set(),
    CANCELLED: set(),
}


class InvalidJobTransition(ValueError):
    pass


@dataclass(frozen=True)
class JobProgress:
    stage: str
    progress: int
    completed_segments: int | None = None
    total_segments: int | None = None


def create_job(
    user_id: int,
    filename: str,
    size: int = 0,
    status: str = UPLOADING,
    upload_id: str | None = None,
) -> str:
    job_id = uuid.uuid4().hex
    database = get_db()
    database.execute(
        """INSERT INTO processing_jobs
           (id, user_id, upload_id, status, original_filename, original_size,
            current_stage, progress)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (job_id, user_id, upload_id, status, filename, size, status),
    )
    database.commit()
    return job_id


def get_job(job_id: str, user_id: int | None = None):
    query = "SELECT * FROM processing_jobs WHERE id = ?"
    parameters: tuple[object, ...] = (job_id,)
    if user_id is not None:
        query += " AND user_id = ?"
        parameters += (user_id,)
    return get_db().execute(query, parameters).fetchone()


def latest_job(user_id: int):
    return get_db().execute(
        "SELECT * FROM processing_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def get_job_for_upload(upload_id: str, user_id: int):
    return get_db().execute(
        """SELECT * FROM processing_jobs
           WHERE upload_id = ? AND user_id = ? ORDER BY created_at LIMIT 1""",
        (upload_id, user_id),
    ).fetchone()


def transition_job(job_id: str, target: str, progress: JobProgress | None = None) -> None:
    database = get_db()
    current = database.execute(
        "SELECT status FROM processing_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if current is None:
        raise LookupError(f"Unknown job {job_id}")
    if target == current["status"]:
        if progress:
            update_progress(job_id, progress)
        return
    if target not in TRANSITIONS[current["status"]]:
        raise InvalidJobTransition(f"Cannot transition {current['status']} to {target}")
    values: list[object] = [target, target, _utc_now()]
    assignments = "status = ?, current_stage = ?, updated_at = ?"
    if progress:
        assignments += ", progress = ?"
        values.append(_validate_percentage(progress.progress))
        if progress.completed_segments is not None:
            assignments += ", completed_segments = ?"
            values.append(progress.completed_segments)
        if progress.total_segments is not None:
            assignments += ", total_segments = ?"
            values.append(progress.total_segments)
    if target == PREPARING_MEDIA:
        assignments += ", started_at = COALESCE(started_at, ?)"
        values.append(_utc_now())
    if target in TERMINAL_STATES:
        assignments += ", completed_at = ?"
        values.append(_utc_now())
    values.append(job_id)
    database.execute(f"UPDATE processing_jobs SET {assignments} WHERE id = ?", values)
    database.commit()


def update_progress(job_id: str, progress: JobProgress) -> None:
    database = get_db()
    fields = ["current_stage = ?", "progress = ?", "updated_at = ?"]
    values: list[object] = [progress.stage, _validate_percentage(progress.progress), _utc_now()]
    if progress.completed_segments is not None:
        fields.append("completed_segments = ?")
        values.append(progress.completed_segments)
    if progress.total_segments is not None:
        fields.append("total_segments = ?")
        values.append(progress.total_segments)
    values.append(job_id)
    database.execute(f"UPDATE processing_jobs SET {', '.join(fields)} WHERE id = ?", values)
    database.commit()


def fail_job(job_id: str, code: str, safe_message: str) -> None:
    database = get_db()
    database.execute(
        """UPDATE processing_jobs SET status = ?, current_stage = ?, error_code = ?,
           safe_error_message = ?, completed_at = ?, updated_at = ? WHERE id = ?""",
        (FAILED, FAILED, code, safe_message, _utc_now(), _utc_now(), job_id),
    )
    database.commit()


def set_media_metadata(job_id: str, media_type: str, duration_seconds: float) -> None:
    database = get_db()
    database.execute(
        """UPDATE processing_jobs
           SET media_type = ?, duration_seconds = ?, updated_at = ? WHERE id = ?""",
        (media_type, duration_seconds, _utc_now(), job_id),
    )
    database.commit()


def create_segments(job_id: str, segments) -> None:
    database = get_db()
    for segment in segments:
        database.execute(
            """INSERT INTO transcription_segments
               (job_id, segment_index, start_seconds, end_seconds, status)
               VALUES (?, ?, ?, ?, 'pending')
               ON CONFLICT(job_id, segment_index) DO UPDATE SET
                 start_seconds = excluded.start_seconds, end_seconds = excluded.end_seconds""",
            (job_id, segment.index, segment.start_seconds, segment.end_seconds),
        )
    database.execute(
        "UPDATE processing_jobs SET total_segments = ?, updated_at = ? WHERE id = ?",
        (len(segments), _utc_now(), job_id),
    )
    database.commit()


def get_segments(job_id: str):
    return get_db().execute(
        "SELECT * FROM transcription_segments WHERE job_id = ? ORDER BY segment_index",
        (job_id,),
    ).fetchall()


def mark_segment_retry(job_id: str, index: int, safe_error: str) -> None:
    database = get_db()
    database.execute(
        """UPDATE transcription_segments SET status = 'pending', retry_count = retry_count + 1,
           last_error = ? WHERE job_id = ? AND segment_index = ?""",
        (safe_error[:500], job_id, index),
    )
    database.commit()


def complete_segment(job_id: str, index: int, transcript: str) -> None:
    database = get_db()
    database.execute(
        """UPDATE transcription_segments SET status = 'completed', transcript = ?,
           last_error = NULL, completed_at = ? WHERE job_id = ? AND segment_index = ?""",
        (transcript, _utc_now(), job_id, index),
    )
    completed = database.execute(
        "SELECT COUNT(*) FROM transcription_segments WHERE job_id = ? AND status = 'completed'",
        (job_id,),
    ).fetchone()[0]
    database.execute(
        "UPDATE processing_jobs SET completed_segments = ?, updated_at = ? WHERE id = ?",
        (completed, _utc_now(), job_id),
    )
    database.commit()


def save_job_result(job_id: str, field: str, value: str) -> None:
    if field not in {"transcript", "summary", "questions"}:
        raise ValueError("Unsupported job result field")
    database = get_db()
    database.execute(
        f"UPDATE processing_jobs SET {field} = ?, updated_at = ? WHERE id = ?",
        (value, _utc_now(), job_id),
    )
    database.commit()


def _validate_percentage(value: int) -> int:
    if not 0 <= value <= 100:
        raise ValueError("Progress must be between 0 and 100")
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
