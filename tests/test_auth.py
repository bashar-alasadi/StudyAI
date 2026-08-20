from conftest import csrf, register_and_login


def test_registration_login_and_dashboard(client):
    response = register_and_login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "طالب تجريبي" in dashboard.text


def test_password_is_not_stored_in_plain_text(app, client):
    register_and_login(client)
    from studyai.db import get_db
    with app.app_context():
        password_hash = get_db().execute("SELECT password_hash FROM users").fetchone()[0]
    assert password_hash != "secure-pass"
    assert password_hash.startswith(("scrypt:", "pbkdf2:"))


def test_csrf_is_required(client):
    client.get("/register")
    response = client.post("/register", data={"username": "student"})
    assert response.status_code == 400


def test_duplicate_account_is_rejected(client):
    register_and_login(client)
    client.post("/logout", data={"csrf_token": csrf(client)})
    client.get("/register")
    response = client.post("/register", data={
        "csrf_token": csrf(client), "name": "Another", "username": "student",
        "email": "other@example.com", "password": "secure-pass",
    })
    assert "مستخدم بالفعل" in response.text
