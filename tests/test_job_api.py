from conftest import csrf, register_and_login

from studyai.db import get_db
from studyai.jobs import UPLOADING, create_job, fail_job, get_job


def owner_id(app):
    with app.app_context():
        return get_db().execute("SELECT id FROM users WHERE username='__public__'").fetchone()[0]


def test_job_status_result_and_latest_are_owner_scoped(app, client):
    register_and_login(client)
    with app.app_context():
        job_id = create_job(owner_id(app), "lecture.mp3", status=UPLOADING)
    assert client.get("/api/jobs/latest").get_json()["job_id"] == job_id
    status = client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    assert status.get_json()["progress"] == 0
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 409

    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO users (name, username, email, password_hash) VALUES (?, ?, ?, ?)",
            ("Other", "other-job-user", "other-job@example.com", "hash"),
        )
        database.commit()
    with client.session_transaction() as session:
        session.pop("admin_authenticated", None)
        session["public_job_ids"] = []
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_failed_job_can_be_retried(app, client):
    register_and_login(client)
    with app.app_context():
        job_id = create_job(owner_id(app), "lecture.mp3", status=UPLOADING)
        fail_job(job_id, "temporary", "حاول مجددًا")
    response = client.post(
        f"/api/jobs/{job_id}/retry", headers={"X-CSRF-Token": csrf(client)}
    )
    assert response.status_code == 202
    with app.app_context():
        assert get_job(job_id)["status"] == "queued"
        assert get_job(job_id)["error_code"] is None


def test_retry_requires_csrf_and_failed_state(app, client):
    register_and_login(client)
    with app.app_context():
        job_id = create_job(owner_id(app), "lecture.mp3", status=UPLOADING)
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 400
    response = client.post(
        f"/api/jobs/{job_id}/retry", headers={"X-CSRF-Token": csrf(client)}
    )
    assert response.status_code == 409
