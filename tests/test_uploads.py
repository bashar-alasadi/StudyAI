from __future__ import annotations

from conftest import csrf, register_and_login

from studyai.db import get_db
from studyai.services.uploads import (
    cleanup_expired_job_media,
    cleanup_stale_uploads,
    upload_dir,
)


def initialize(client, filename="lecture.mp3", total_size=10, chunk_size=4):
    return client.post(
        "/api/uploads",
        json={"filename": filename, "total_size": total_size, "chunk_size": chunk_size},
        headers={"X-CSRF-Token": csrf(client)},
    )


def put_chunk(client, upload_id, index, content):
    return client.put(
        f"/api/uploads/{upload_id}/chunks/{index}",
        data=content,
        content_type="application/octet-stream",
        headers={"X-CSRF-Token": csrf(client)},
    )


def test_resumable_upload_and_idempotent_completion(app, client):
    register_and_login(client)
    initialized = initialize(client)
    assert initialized.status_code == 201
    upload = initialized.get_json()
    assert upload["expected_chunks"] == 3
    upload_id = upload["upload_id"]

    assert put_chunk(client, upload_id, 1, b"efgh").status_code == 200
    assert put_chunk(client, upload_id, 0, b"abcd").status_code == 200
    duplicate = put_chunk(client, upload_id, 0, b"abcd")
    assert duplicate.status_code == 200
    assert client.get(f"/api/uploads/{upload_id}").get_json()["received_chunks"] == 2

    incomplete = client.post(
        f"/api/uploads/{upload_id}/complete", headers={"X-CSRF-Token": csrf(client)}
    )
    assert incomplete.status_code == 409
    assert put_chunk(client, upload_id, 2, b"ij").status_code == 200

    completed = client.post(
        f"/api/uploads/{upload_id}/complete", headers={"X-CSRF-Token": csrf(client)}
    )
    assert completed.status_code == 202
    repeated = client.post(
        f"/api/uploads/{upload_id}/complete", headers={"X-CSRF-Token": csrf(client)}
    )
    assert repeated.get_json()["job_id"] == completed.get_json()["job_id"]
    with app.app_context():
        row = get_db().execute(
            "SELECT assembled_path FROM upload_sessions WHERE id = ?", (upload_id,)
        ).fetchone()
        with open(row["assembled_path"], "rb") as assembled:
            assert assembled.read() == b"abcdefghij"
        assert get_db().execute(
            "SELECT COUNT(*) FROM upload_chunks WHERE upload_id = ?", (upload_id,)
        ).fetchone()[0] == 0


def test_file_completion_persists_selected_transcription_mode(app, client):
    register_and_login(client)
    upload = initialize(client, total_size=4, chunk_size=4).get_json()
    assert put_chunk(client, upload["upload_id"], 0, b"data").status_code == 200
    completed = client.post(
        f"/api/uploads/{upload['upload_id']}/complete?verbatim_transcript=0",
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert completed.status_code == 202
    with app.app_context():
        row = get_db().execute(
            "SELECT verbatim_transcript FROM processing_jobs WHERE id = ?",
            (completed.get_json()["job_id"],),
        ).fetchone()
        assert row["verbatim_transcript"] == 0


def test_assembly_resumes_after_disk_pressure_without_second_full_copy(
    app, client, monkeypatch
):
    register_and_login(client)
    upload = initialize(client, total_size=8, chunk_size=4).get_json()
    upload_id = upload["upload_id"]
    assert put_chunk(client, upload_id, 0, b"abcd").status_code == 200
    assert put_chunk(client, upload_id, 1, b"efgh").status_code == 200

    calls = 0

    def interrupt_after_first_chunk(_additional_bytes):
        nonlocal calls
        calls += 1
        if calls == 2:
            from studyai.services.uploads import UploadError

            raise UploadError("لا توجد مساحة تخزين كافية لإكمال الرفع.", 507)

    monkeypatch.setattr("studyai.services.uploads._ensure_disk_space", interrupt_after_first_chunk)
    failed = client.post(
        f"/api/uploads/{upload_id}/complete", headers={"X-CSRF-Token": csrf(client)}
    )
    assert failed.status_code == 507
    with app.app_context():
        directory = upload_dir(upload_id)
        assert (directory / "source.mp3.part").read_bytes() == b"abcd"
        assert not (directory / "chunk-00000000").exists()

    monkeypatch.setattr("studyai.services.uploads._ensure_disk_space", lambda _size: None)
    completed = client.post(
        f"/api/uploads/{upload_id}/complete", headers={"X-CSRF-Token": csrf(client)}
    )
    assert completed.status_code == 202
    with app.app_context():
        directory = upload_dir(upload_id)
        assert (directory / "source.mp3").read_bytes() == b"abcdefgh"


def test_upload_continues_when_runtime_cannot_report_disk_capacity(
    app, client, monkeypatch
):
    register_and_login(client)
    monkeypatch.setattr(
        "studyai.services.uploads.shutil.disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError(58, "Not supported")),
    )
    response = initialize(client, total_size=4, chunk_size=4)
    assert response.status_code == 201


def test_upload_rejects_invalid_inputs(client):
    register_and_login(client)
    assert initialize(client, "notes.txt").status_code == 415
    assert initialize(client, total_size=0).status_code == 400
    assert initialize(client, total_size=2048).status_code == 413
    response = initialize(client, total_size=10)
    upload_id = response.get_json()["upload_id"]
    assert put_chunk(client, upload_id, 0, b"too-long").status_code == 413
    assert put_chunk(client, upload_id, 9, b"data").status_code == 400


def test_android_and_telegram_audio_formats_are_accepted(client):
    register_and_login(client)
    for filename in ("voice.opus", "recording.aac", "lecture.3gp", "telegram.oga"):
        assert initialize(client, filename, total_size=4, chunk_size=4).status_code == 201


def test_android_content_uri_filename_uses_reported_audio_mime(client):
    register_and_login(client)
    response = client.post(
        "/api/uploads",
        json={
            "filename": "recording",
            "total_size": 4,
            "chunk_size": 4,
            "mime_type": "audio/ogg",
        },
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 201


def test_conflicting_duplicate_chunk_is_rejected(client):
    register_and_login(client)
    upload_id = initialize(client).get_json()["upload_id"]
    assert put_chunk(client, upload_id, 0, b"abcd").status_code == 200
    assert put_chunk(client, upload_id, 0, b"wxyz").status_code == 409


def test_upload_is_private_to_browser_session(app, client):
    register_and_login(client)
    upload_id = initialize(client).get_json()["upload_id"]
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO users (name, username, email, password_hash) VALUES (?, ?, ?, ?)",
            ("Other", "other", "other@example.com", "hash"),
        )
        database.commit()
    with client.session_transaction() as session:
        session.pop("admin_authenticated", None)
        session["public_upload_ids"] = []
    assert client.get(f"/api/uploads/{upload_id}").status_code == 404
    assert put_chunk(client, upload_id, 0, b"abcd").status_code == 404


def test_upload_mutations_require_csrf(client):
    register_and_login(client)
    response = client.post(
        "/api/uploads", json={"filename": "lecture.mp3", "total_size": 10, "chunk_size": 4}
    )
    assert response.status_code == 400


def test_stale_incomplete_upload_cleanup(app, client):
    register_and_login(client)
    upload_id = initialize(client).get_json()["upload_id"]
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE upload_sessions SET updated_at = datetime('now', '-48 hours') WHERE id = ?",
            (upload_id,),
        )
        database.commit()
        assert cleanup_stale_uploads(24) == 1
        assert not upload_dir(upload_id).exists()
        assert database.execute(
            "SELECT 1 FROM upload_sessions WHERE id = ?", (upload_id,)
        ).fetchone() is None


def test_expired_failed_job_media_cleanup_preserves_records(app):
    from studyai.jobs import create_job, fail_job
    from studyai.services.uploads import create_upload

    with app.app_context():
        database = get_db()
        database.execute(
            """INSERT INTO users (name, username, email, password_hash)
               VALUES ('Test', 'cleanup-user', 'cleanup@example.com', 'unused')"""
        )
        database.commit()
        user_id = database.execute(
            "SELECT id FROM users WHERE username = 'cleanup-user'"
        ).fetchone()["id"]
        upload = create_upload(user_id, "lecture.mp3", 4, 4)
        directory = upload_dir(upload["id"])
        job_id = create_job(user_id, "lecture.mp3", upload_id=upload["id"])
        fail_job(job_id, "provider_error", "تعذر إكمال المعالجة")
        database.execute(
            "UPDATE processing_jobs SET completed_at = datetime('now', '-8 days') WHERE id = ?",
            (job_id,),
        )
        database.commit()

        assert cleanup_expired_job_media(168) == 1
        assert not directory.exists()
        assert database.execute(
            "SELECT id FROM processing_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert database.execute(
            "SELECT assembled_path FROM upload_sessions WHERE id = ?", (upload["id"],)
        ).fetchone()["assembled_path"] is None


def test_queue_failure_is_persisted_as_retryable_job_failure(app, client):
    class BrokenQueue:
        def enqueue(self, _job_id):
            raise ConnectionError("redis unavailable")

    register_and_login(client)
    upload_id = initialize(client, total_size=4, chunk_size=4).get_json()["upload_id"]
    assert put_chunk(client, upload_id, 0, b"data").status_code == 200
    app.extensions["job_queue"] = BrokenQueue()

    response = client.post(
        f"/api/uploads/{upload_id}/complete", headers={"X-CSRF-Token": csrf(client)}
    )

    assert response.status_code == 503
    with app.app_context():
        job = get_db().execute(
            "SELECT status, error_code FROM processing_jobs WHERE upload_id = ?", (upload_id,)
        ).fetchone()
        assert (job["status"], job["error_code"]) == ("failed", "queue_unavailable")
