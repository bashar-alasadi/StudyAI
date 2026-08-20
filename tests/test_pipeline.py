from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import register_and_login

from studyai.db import get_db
from studyai.jobs import (
    QUEUED,
    TRANSCRIBING,
    UPLOADED,
    JobProgress,
    complete_segment,
    create_job,
    create_segments,
    get_job,
    get_segments,
    transition_job,
)
from studyai.pipeline import CompletenessError, assemble_complete_transcript, process_pipeline
from studyai.services.ai import AIServiceError
from studyai.services.media import MediaInfo, SegmentFile
from studyai.services.uploads import complete_upload, create_upload, save_chunk, upload_dir


class FakeMedia:
    def check_dependencies(self):
        pass

    def inspect(self, _source):
        return MediaInfo(65, "video", "mp4")

    def normalize_audio(self, _source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"normalized")

    def segment_audio(self, _source, destination, _duration, _segment, _overlap):
        values = []
        for index, (start, end) in enumerate(((0, 35), (30, 65), (60, 65))):
            path = destination / f"segment-{index:05d}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            values.append(SegmentFile(index, path, start, end))
        return values


class FakePipelineAI:
    def __init__(self, failures=None):
        self.failures = dict(failures or {})
        self.calls = []

    def count_tokens(self, text):
        return len(text)

    def transcribe_path(self, path):
        index = int(path.stem.split("-")[-1])
        self.calls.append(index)
        if self.failures.get(index, 0):
            self.failures[index] -= 1
            raise AIServiceError(
                "temporary", "مؤقت", retryable=True, code="provider_transient"
            )
        return {
            0: "بداية المحاضرة مفهوم مهم في الوسط",
            1: "مفهوم مهم في الوسط شرح إضافي ثم النهاية",
            2: "شرح إضافي ثم النهاية خاتمة المحاضرة",
        }[index]

    def summarize(self, text):
        assert "بداية المحاضرة" in text and "خاتمة المحاضرة" in text
        return "ملخص شامل"

    def generate_questions(self, text):
        assert "بداية المحاضرة" in text and "خاتمة المحاضرة" in text
        return "أسئلة شاملة"


def make_queued_job(app, client):
    register_and_login(client)
    with app.app_context():
        owner = get_db().execute("SELECT id FROM users WHERE username='student'").fetchone()[0]
        upload = create_upload(owner, "lecture.mp4", 4, 4)
        save_chunk(upload["id"], owner, 0, io.BytesIO(b"data"))
        complete_upload(upload["id"], owner)
        job_id = create_job(owner, "lecture.mp4", 4, UPLOADED, upload["id"])
        transition_job(job_id, QUEUED)
        return job_id


def test_full_multisegment_pipeline_completes_and_cleans_up(app, client):
    job_id = make_queued_job(app, client)
    ai = FakePipelineAI()
    app.config.update(MEDIA_SERVICE_FACTORY=FakeMedia, AI_SERVICE_FACTORY=lambda: ai)
    with app.app_context():
        storage = upload_dir(get_job(job_id)["upload_id"])
        process_pipeline(job_id, sleeper=lambda _seconds: None)
        job = get_job(job_id)
        assert job["status"] == "completed"
        assert job["completed_segments"] == job["total_segments"] == 3
        assert "بداية المحاضرة" in job["transcript"]
        assert "خاتمة المحاضرة" in job["transcript"]
        assert job["summary"] == "ملخص شامل"
        assert job["questions"] == "أسئلة شاملة"
        assert not storage.exists()


def test_segment_retry_does_not_repeat_successful_segments(app, client):
    job_id = make_queued_job(app, client)
    ai = FakePipelineAI({1: 1})
    app.config.update(MEDIA_SERVICE_FACTORY=FakeMedia, AI_SERVICE_FACTORY=lambda: ai)
    with app.app_context():
        process_pipeline(job_id, sleeper=lambda _seconds: None)
        assert ai.calls == [0, 1, 1, 2]
        assert get_segments(job_id)[1]["retry_count"] == 1
        assert get_job(job_id)["status"] == "completed"


def test_worker_resume_skips_completed_segment(app, client):
    job_id = make_queued_job(app, client)
    app.config.update(MEDIA_SERVICE_FACTORY=FakeMedia)
    with app.app_context():
        upload = get_job(job_id)["upload_id"]
        segment_dir = upload_dir(upload) / "work" / "segments"
        files = FakeMedia().segment_audio(Path(), segment_dir, 65, 30, 5)
        create_segments(job_id, files)
        transition_job(job_id, TRANSCRIBING, JobProgress(TRANSCRIBING, 30, 1, 3))
        complete_segment(job_id, 0, "بداية المحاضرة مفهوم مهم في الوسط")
        ai = FakePipelineAI()
        app.config["AI_SERVICE_FACTORY"] = lambda: ai
        process_pipeline(job_id, sleeper=lambda _seconds: None)
        assert ai.calls == [1, 2]
        assert get_job(job_id)["status"] == "completed"


def test_missing_or_failed_segment_prevents_assembly():
    rows = [
        {"segment_index": 0, "status": "completed", "transcript": "أول"},
        {"segment_index": 2, "status": "completed", "transcript": "ثالث"},
    ]
    with pytest.raises(CompletenessError):
        assemble_complete_transcript(rows)
    sequential_but_short = [
        {"segment_index": 0, "status": "completed", "transcript": "أول"},
        {"segment_index": 1, "status": "completed", "transcript": "ثان"},
    ]
    with pytest.raises(CompletenessError):
        assemble_complete_transcript(sequential_but_short, expected_total=3)
    rows[1] = {"segment_index": 1, "status": "pending", "transcript": None}
    with pytest.raises(CompletenessError):
        assemble_complete_transcript(rows)


def test_overlap_is_deduplicated_and_arabic_is_preserved():
    rows = [
        {"segment_index": 0, "status": "completed", "transcript": "ألف باء جيم دال"},
        {"segment_index": 1, "status": "completed", "transcript": "باء جيم دال هاء واو"},
    ]
    assert assemble_complete_transcript(rows) == "ألف باء جيم دال هاء واو"
