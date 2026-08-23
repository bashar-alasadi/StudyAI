from conftest import csrf, register_and_login


def test_login_rotates_csrf_token(client):
    client.get("/admin/login")
    original = csrf(client)
    register_and_login(client)
    assert csrf(client) != original


def test_invalid_csrf_is_rejected_for_json_and_chunk_requests(client):
    register_and_login(client)
    invalid = {"X-CSRF-Token": "invalid-token"}
    assert client.post("/api/uploads", json={}, headers=invalid).status_code == 400
    assert client.post("/api/jobs/unknown/retry", headers=invalid).status_code == 400


def test_invalid_login_and_logout(client):
    client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={"csrf_token": csrf(client), "username": "missing", "password": "wrong-pass"},
    )
    assert "غير صحيحة" in response.text
    register_and_login(client)
    response = client.post("/logout", data={"csrf_token": csrf(client)})
    assert response.status_code == 302
    assert client.get("/dashboard").status_code == 200
    assert client.get("/admin/").status_code == 302
