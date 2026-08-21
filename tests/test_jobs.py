import pytest
from conftest import register_and_login

from studyai.db import get_db
from studyai.jobs import (
    ASSEMBLING,
    PREPARING_MEDIA,
    QUEUED,
    SEGMENTING,
    TRANSCRIBING,
    UPLOADED,
    UPLOADING,
    InvalidJobTransition,
    JobProgress,
    create_job,
    get_job,
    latest_job,
    prepare_interrupted_resume,
    transition_job,
    update_progress,
)
from studyai.queueing import SynchronousJobQueue


def user_id(app):
    with app.app_context():
        return get_db().execute("SELECT id FROM users WHERE username = 'student'").fetchone()[0]


def test_job_state_machine_and_progress(app, client):
    register_and_login(client)
    with app.app_context():
        job_id = create_job(user_id(app), "lecture.mp4", 1024)
        assert get_job(job_id)["status"] == UPLOADING
        transition_job(job_id, UPLOADED)
        transition_job(job_id, QUEUED)
        transition_job(job_id, PREPARING_MEDIA)
        transition_job(job_id, SEGMENTING)
        transition_job(job_id, TRANSCRIBING, JobProgress(TRANSCRIBING, 30, 3, 10))
        update_progress(job_id, JobProgress(TRANSCRIBING, 40, 4, 10))
        transition_job(job_id, ASSEMBLING)
        job = get_job(job_id)
        assert job["completed_segments"] == 4
        assert job["total_segments"] == 10
        assert job["progress"] == 40


def test_invalid_transition_is_rejected(app, client):
    register_and_login(client)
    with app.app_context():
        job_id = create_job(user_id(app), "lecture.mp3")
        with pytest.raises(InvalidJobTransition):
            transition_job(job_id, TRANSCRIBING)


def test_jobs_are_user_scoped(app, client):
    register_and_login(client)
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO users (name, username, email, password_hash) VALUES (?, ?, ?, ?)",
            ("Other", "other", "other@example.com", "hash"),
        )
        database.commit()
        owner = user_id(app)
        other = database.execute("SELECT id FROM users WHERE username = 'other'").fetchone()[0]
        job_id = create_job(owner, "private.mp3")
        assert get_job(job_id, owner) is not None
        assert get_job(job_id, other) is None
        assert latest_job(owner)["id"] == job_id


def test_synchronous_queue_is_testable():
    calls = []
    queue = SynchronousJobQueue(calls.append)
    assert queue.enqueue("job-id") == "sync:job-id"
    assert calls == ["job-id"]


def test_rq_job_id_uses_only_rq_safe_characters():
    from studyai.queueing import rq_job_id

    assert rq_job_id("abc123") == "studyai-abc123"


def test_windows_worker_uses_native_waitpid(monkeypatch):
    from studyai.windows_worker import WindowsSpawnWorker

    worker = object.__new__(WindowsSpawnWorker)
    worker._horse_pid = 42
    monkeypatch.setattr("studyai.windows_worker.os.waitpid", lambda pid, options: (pid, 0))
    assert worker.wait_for_horse() == (42, 0, None)


def test_interrupted_resume_preserves_completed_segments(app, client):
    register_and_login(client)
    with app.app_context():
        job_id = create_job(user_id(app), "lecture.mp3", status=QUEUED)
        database = get_db()
        database.execute(
            """UPDATE processing_jobs SET status = 'generating_questions',
               current_stage = 'generating_questions', completed_segments = 2,
               total_segments = 3 WHERE id = ?""",
            (job_id,),
        )
        database.commit()

        prepare_interrupted_resume(job_id)

        job = get_job(job_id)
        assert job["status"] == QUEUED
        assert job["completed_segments"] == 2
        assert job["total_segments"] == 3
