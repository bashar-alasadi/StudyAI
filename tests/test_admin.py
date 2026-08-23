from conftest import csrf, register_and_login


def make_admin(app):
    from studyai.db import get_db

    with app.app_context():
        database = get_db()
        database.execute("UPDATE users SET is_admin = 1 WHERE username = 'student'")
        database.commit()


def test_admin_dashboard_requires_admin(app, client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")
    register_and_login(client)
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "لوحة الإدارة" in response.text


def test_admin_can_suspend_and_promote_user(app, client):
    register_and_login(client)
    make_admin(app)
    client.get("/register")
    from studyai.db import get_db

    with app.app_context():
        database = get_db()
        database.execute(
            """INSERT INTO users (name, username, email, password_hash)
               VALUES ('مستخدم ثان', 'second', 'second@example.com', 'unused')"""
        )
        database.commit()
        user_id = database.execute("SELECT id FROM users WHERE username = 'second'").fetchone()[0]

    response = client.post(
        f"/admin/users/{user_id}/toggle-admin", data={"csrf_token": csrf(client)}
    )
    assert response.status_code == 302
    client.post(f"/admin/users/{user_id}/toggle-active", data={"csrf_token": csrf(client)})
    with app.app_context():
        user = (
            get_db()
            .execute("SELECT is_admin, is_active FROM users WHERE id = ?", (user_id,))
            .fetchone()
        )
        assert user["is_admin"] == 1
        assert user["is_active"] == 0


def test_admin_provider_key_is_encrypted_and_selected(app, client):
    register_and_login(client)
    make_admin(app)
    response = client.post(
        "/admin/providers",
        data={
            "csrf_token": csrf(client),
            "name": "Gemini الرئيسي",
            "provider_type": "gemini",
            "model": "gemini-test-model",
            "api_key": "very-secret-provider-key",
        },
    )
    assert response.status_code == 302

    from studyai.db import get_db
    from studyai.providers import resolve_ai_config

    with app.app_context():
        database = get_db()
        provider = database.execute("SELECT * FROM ai_providers").fetchone()
        assert "very-secret-provider-key" not in provider["api_key_encrypted"]
        client.post(
            f"/admin/providers/{provider['id']}/activate",
            data={"csrf_token": csrf(client)},
        )
        resolved = resolve_ai_config(app.config)
        assert resolved["GEMINI_API_KEY"] == "very-secret-provider-key"
        assert resolved["GEMINI_MODEL"] == "gemini-test-model"


def test_admin_cannot_remove_own_access(app, client):
    register_and_login(client)
    make_admin(app)
    with app.app_context():
        from studyai.db import get_db

        user_id = get_db().execute(
            "SELECT id FROM users WHERE username = '__public__'"
        ).fetchone()[0]
    client.post(f"/admin/users/{user_id}/toggle-admin", data={"csrf_token": csrf(client)})
    with app.app_context():
        row = get_db().execute(
            "SELECT is_admin FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        assert row[0] == 0
