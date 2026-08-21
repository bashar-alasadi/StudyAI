from conftest import csrf, register_and_login
from werkzeug.security import check_password_hash


def test_registration_login_and_dashboard(client):
    response = register_and_login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "طالب تجريبي" in dashboard.text


def test_login_accepts_email(client):
    register_and_login(client)
    client.post("/logout", data={"csrf_token": csrf(client)})
    client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(client),
            "username": "STUDENT@EXAMPLE.COM",
            "password": "secure-pass",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


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
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf(client),
            "name": "Another",
            "username": "student",
            "email": "other@example.com",
            "password": "secure-pass",
        },
    )
    assert "مستخدم بالفعل" in response.text


def test_password_reset_changes_password_and_invalidates_link(app, client):
    register_and_login(client)
    client.post("/logout", data={"csrf_token": csrf(client)})
    client.get("/forgot-password")
    response = client.post(
        "/forgot-password",
        data={
            "csrf_token": csrf(client),
            "email": "student@example.com",
        },
    )
    assert response.status_code == 200
    assert "إذا كان البريد مسجلًا" in response.text
    marker = 'href="http://localhost/reset-password/'
    assert marker in response.text
    token = response.text.split(marker, 1)[1].split('"', 1)[0]

    reset_path = f"/reset-password/{token}"
    response = client.post(
        reset_path,
        data={
            "csrf_token": csrf(client),
            "password": "new-secure-pass",
            "password_confirmation": "new-secure-pass",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert client.get(reset_path).headers["Location"].endswith("/forgot-password")

    from studyai.db import get_db

    with app.app_context():
        password_hash = (
            get_db()
            .execute("SELECT password_hash FROM users WHERE username = 'student'")
            .fetchone()[0]
        )
    assert check_password_hash(password_hash, "new-secure-pass")


def test_password_reset_does_not_reveal_unknown_email(client):
    client.get("/forgot-password")
    response = client.post(
        "/forgot-password",
        data={
            "csrf_token": csrf(client),
            "email": "unknown@example.com",
        },
    )
    assert response.status_code == 200
    assert "إذا كان البريد مسجلًا" in response.text
    assert "افتح رابط الاستعادة" not in response.text


def test_password_reset_does_not_reveal_mail_delivery_failure(client, monkeypatch):
    register_and_login(client)
    client.post("/logout", data={"csrf_token": csrf(client)})
    monkeypatch.setattr(
        "studyai.auth._deliver_reset_email",
        lambda *_args: (_ for _ in ()).throw(OSError("SMTP unavailable")),
    )
    client.get("/forgot-password")
    response = client.post(
        "/forgot-password",
        data={"csrf_token": csrf(client), "email": "student@example.com"},
    )
    assert response.status_code == 200
    assert "إذا كان البريد مسجلًا" in response.text
    assert "افتح رابط الاستعادة" not in response.text


def test_invalid_password_reset_token_is_rejected(client):
    response = client.get("/reset-password/not-a-valid-token")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/forgot-password")
