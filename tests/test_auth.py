from conftest import csrf


def test_students_use_dashboard_without_account(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "لا تحتاج إلى تسجيل" in response.text
    assert "دخول الإدارة" not in response.text
    assert "/admin/login" not in response.text


def test_registration_is_removed(client):
    assert client.get("/register").status_code == 404
    assert client.post("/register").status_code in {400, 405}


def test_legacy_login_redirects_to_admin_login(client):
    response = client.get("/login")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_admin_login_and_logout(client):
    client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={
            "csrf_token": csrf(client),
            "username": "admin",
            "password": "admin-test-pass",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200
    response = client.post("/logout", data={"csrf_token": csrf(client)})
    assert response.status_code == 302
    assert client.get("/admin/").headers["Location"].endswith("/admin/login")


def test_invalid_admin_credentials_are_rejected(client):
    client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={"csrf_token": csrf(client), "username": "admin", "password": "wrong"},
    )
    assert response.status_code == 200
    assert "بيانات دخول الإدارة غير صحيحة" in response.text


def test_admin_login_requires_csrf(client):
    response = client.post(
        "/admin/login", data={"username": "admin", "password": "admin-test-pass"}
    )
    assert response.status_code == 400
