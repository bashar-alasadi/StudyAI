from conftest import csrf, register_and_login


def test_api_requires_authentication(client):
    client.get("/")
    response = client.post("/api/jobs/unknown/retry", headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 401


def test_synchronous_phase_one_endpoints_are_removed(client):
    register_and_login(client)
    for path in ("/api/transcriptions", "/api/summaries", "/api/questions"):
        assert client.post(path, headers={"X-CSRF-Token": csrf(client)}).status_code == 404
