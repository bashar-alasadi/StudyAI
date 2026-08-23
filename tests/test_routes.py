def test_home_and_health(client):
    home = client.get("/")
    assert home.status_code == 200
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    assert home.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in home.headers["Content-Security-Policy"]
    assert client.get("/health").get_json() == {"status": "ok"}


def test_dependency_health_does_not_expose_connection_details(client):
    response = client.get("/health/dependencies")
    assert response.status_code in {200, 503}
    payload = response.get_json()
    assert set(payload) == {"application", "database", "ai", "redis", "ffmpeg", "ffprobe"}
    assert "redis://" not in response.text


def test_dashboard_is_public(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "لا تحتاج إلى تسجيل" in response.text


def test_unknown_page_has_friendly_error(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert "تعذر إكمال الطلب" in response.text
