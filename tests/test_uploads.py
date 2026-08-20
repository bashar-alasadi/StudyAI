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


def test_upload_rejects_invalid_inputs(client):
    register_and_login(client)
    assert initialize(client, "notes.txt").status_code == 415
    assert initialize(client, total_size=0).status_code == 400
    assert initialize(client, total_size=2048).status_code == 413
    response = initialize(client, total_size=10)
    upload_id = response.get_json()["upload_id"]
    assert put_chunk(client, upload_id, 0, b"too-long").status_code == 413
    assert put_chunk(client, upload_id, 9, b"data").status_code == 400


def test_conflicting_duplicate_chunk_is_rejected(client):
    register_and_login(client)
    upload_id = initialize(client).get_json()["upload_id"]
    assert put_chunk(client, upload_id, 0, b"abcd").status_code == 200
    assert put_chunk(client, upload_id, 0, b"wxyz").status_code == 409


def test_upload_is_private_to_owner(app, client):
    register_and_login(client)
    upload_id = initialize(client).get_json()["upload_id"]
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO users (name, username, email, password_hash) VALUES (?, ?, ?, ?)",
            ("Other", "other", "other@example.com", "hash"),
        )
        other_id = database.execute("SELECT id FROM users WHERE username='other'").fetchone()[0]
        database.commit()
    with client.session_transaction() as session:
        session["user_id"] = other_id
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
