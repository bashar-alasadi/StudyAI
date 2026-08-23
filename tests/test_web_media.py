from __future__ import annotations

import pytest
from conftest import csrf, register_and_login

from studyai.db import get_db
from studyai.services.web_media import WebMediaError, validate_source_url


def test_url_submission_creates_owned_queued_job(client, monkeypatch):
    register_and_login(client)
    monkeypatch.setattr(
        "studyai.services.web_media.socket.getaddrinfo",
        lambda *_args: [(2, 1, 6, "", ("142.250.74.110", 0))],
    )

    response = client.post(
        "/api/uploads/url",
        json={
            "url": "https://www.youtube.com/watch?v=example",
            "include_explanations": True,
        },
        headers={"X-CSRF-Token": csrf(client)},
    )

    assert response.status_code == 202
    with client.application.app_context():
        job = get_db().execute(
            """SELECT status, source_url, upload_id, include_explanations
               FROM processing_jobs WHERE id = ?""",
            (response.get_json()["job_id"],),
        ).fetchone()
        assert job["status"] == "queued"
        assert job["source_url"].startswith("https://www.youtube.com/")
        assert job["upload_id"] is None
        assert job["include_explanations"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private.mp3",
        "http://localhost/private.mp3",
        "file:///etc/passwd",
        "https://user@example.com/lecture.mp3",
        "https://example.com:8080/lecture.mp3",
    ],
)
def test_private_or_unsafe_source_urls_are_rejected(url):
    with pytest.raises(WebMediaError):
        validate_source_url(url)
