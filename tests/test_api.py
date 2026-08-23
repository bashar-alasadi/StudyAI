from conftest import csrf, register_and_login


def test_unknown_public_job_is_hidden(client):
    client.get("/")
    response = client.post("/api/jobs/unknown/retry", headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 404


def test_synchronous_phase_one_endpoints_are_removed(client):
    register_and_login(client)
    for path in ("/api/transcriptions", "/api/summaries", "/api/questions"):
        assert client.post(path, headers={"X-CSRF-Token": csrf(client)}).status_code == 404
