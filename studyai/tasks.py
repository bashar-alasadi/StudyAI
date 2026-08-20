"""RQ task entry points."""

from __future__ import annotations


def process_lecture_job(job_id: str) -> None:
    from app import app
    from studyai.pipeline import process_pipeline

    with app.app_context():
        process_pipeline(job_id)
