from __future__ import annotations

import pytest

from studyai import create_app
from studyai.config import build_config

ENVIRONMENT_KEYS = ("APP_ENV", "SECRET_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "DATABASE_PATH")


def clear_environment(monkeypatch):
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_dotenv_is_loaded_before_configuration(monkeypatch, tmp_path):
    clear_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "APP_ENV=production\nSECRET_KEY=dotenv-production-secret\n"
        "GEMINI_API_KEY=dotenv-gemini-key\nGEMINI_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3")})
    assert app.config["ENVIRONMENT"] == "production"
    assert app.config["SECRET_KEY"] == "dotenv-production-secret"
    assert app.config["GEMINI_API_KEY"] == "dotenv-gemini-key"
    assert app.config["GEMINI_MODEL"] == "dotenv-model"
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_os_environment_takes_precedence_over_dotenv(monkeypatch, tmp_path):
    clear_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_MODEL=dotenv-model\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_MODEL", "operating-system-model")
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3")})
    assert app.config["GEMINI_MODEL"] == "operating-system-model"


def test_production_requires_a_real_secret(monkeypatch, tmp_path):
    clear_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"DATABASE": str(tmp_path / "test.sqlite3")})


def test_test_config_remains_injectable(monkeypatch, tmp_path):
    clear_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "injected-test-secret",
        "DATABASE": str(tmp_path / "injected.sqlite3"),
    })
    assert app.config["SECRET_KEY"] == "injected-test-secret"


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_invalid_upload_limit_fails_safely(value):
    with pytest.raises(RuntimeError, match="MAX_UPLOAD_MB"):
        build_config({"MAX_UPLOAD_MB": value})
