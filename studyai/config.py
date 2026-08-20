"""Central configuration evaluated only after dotenv has been loaded."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DEVELOPMENT_SECRET = "development-only-change-me"


def build_config(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Build a fresh settings mapping from the current environment."""
    env = os.environ if environ is None else environ
    environment = env.get("APP_ENV", "development").strip().lower()
    return {
        "ENVIRONMENT": environment,
        "SECRET_KEY": env.get("SECRET_KEY", DEVELOPMENT_SECRET),
        "DATABASE": env.get("DATABASE_PATH", str(Path("instance") / "studyai.sqlite3")),
        "GEMINI_API_KEY": env.get("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": env.get("GEMINI_MODEL", "gemini-3.6-flash"),
        "REDIS_URL": env.get("REDIS_URL", "redis://localhost:6379/0"),
        "RQ_QUEUE": env.get("RQ_QUEUE", "studyai"),
        "JOB_QUEUE_MODE": env.get("JOB_QUEUE_MODE", "rq"),
        "JOB_TIMEOUT_SECONDS": _positive_int(env, "JOB_TIMEOUT_SECONDS", 21600),
        "MAX_CONTENT_LENGTH": _positive_int(env, "MAX_UPLOAD_MB", 100) * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": environment == "production",
    }


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw_value = env.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value
