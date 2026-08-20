import io

from conftest import csrf, register_and_login


def test_api_requires_authentication(client):
    client.get("/")
    response = client.post(
        "/api/summaries",
        json={"text": "نص طويل " * 10},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 401


def test_transcription_flow(client):
    register_and_login(client)
    response = client.post(
        "/api/transcriptions",
        data={"audio": (io.BytesIO(b"fake audio"), "lecture.mp3")},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 201
    assert "نص محاضرة" in response.get_json()["text"]


def test_unsupported_upload_is_rejected(client):
    register_and_login(client)
    response = client.post(
        "/api/transcriptions",
        data={"audio": (io.BytesIO(b"content"), "notes.txt")},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 415


def test_summary_and_questions(client):
    register_and_login(client)
    headers = {"X-CSRF-Token": csrf(client)}
    text = "هذا نص محاضرة طويل ومفيد للاختبار. " * 3
    summary = client.post("/api/summaries", json={"text": text}, headers=headers)
    questions = client.post("/api/questions", json={"text": text}, headers=headers)
    assert summary.get_json()["result"] == "ملخص تجريبي"
    assert "سؤال تجريبي" in questions.get_json()["result"]


def test_short_text_is_rejected(client):
    register_and_login(client)
    response = client.post(
        "/api/summaries",
        json={"text": "قصير"},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 400
