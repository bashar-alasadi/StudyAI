"""RQ task entry points. Pipeline implementation is injected in later stages."""

from __future__ import annotations


def process_lecture_job(job_id: str) -> None:
    from app import app
    from studyai.jobs import fail_job

    with app.app_context():
        fail_job(job_id, "pipeline_not_ready", "خط المعالجة غير متاح مؤقتًا.")
