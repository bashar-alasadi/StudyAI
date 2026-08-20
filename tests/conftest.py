from __future__ import annotations

import pytest

from studyai import create_app


class FakeAIService:
    def transcribe(self, _stream, _extension):
        return "هذا نص محاضرة تجريبي طويل بما يكفي للاختبار."

    def summarize(self, _text):
        return "ملخص تجريبي"

    def generate_questions(self, _text):
        return "سؤال تجريبي؟\nالإجابة التجريبية"


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "AI_SERVICE_FACTORY": FakeAIService,
        "JOB_QUEUE_MODE": "sync",
        "UPLOAD_ROOT": str(tmp_path / "uploads"),
        "MAX_UPLOAD_SIZE_BYTES": 1024,
        "UPLOAD_CHUNK_SIZE_BYTES": 4,
        "MAX_CONTENT_LENGTH": 1024,
        "MIN_FREE_DISK_MB": 0,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["csrf_token"]


def register_and_login(client):
    client.get("/register")
    client.post("/register", data={
        "csrf_token": csrf(client), "name": "طالب تجريبي", "username": "student",
        "email": "student@example.com", "password": "secure-pass",
    })
    client.get("/login")
    return client.post("/login", data={
        "csrf_token": csrf(client), "username": "student", "password": "secure-pass",
    })
