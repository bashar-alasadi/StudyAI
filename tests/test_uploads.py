from __future__ import annotations

from conftest import csrf, register_and_login

from studyai.db import get_db


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
