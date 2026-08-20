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
        "UPLOAD_ROOT": env.get("UPLOAD_ROOT", str(Path("instance") / "uploads")),
        "MAX_UPLOAD_SIZE_BYTES": _positive_int(env, "MAX_UPLOAD_GB", 5) * 1024**3,
        "UPLOAD_CHUNK_SIZE_BYTES": _positive_int(env, "UPLOAD_CHUNK_MB", 8) * 1024**2,
        "MAX_CONTENT_LENGTH": _positive_int(env, "UPLOAD_CHUNK_MB", 8) * 1024**2 + 1024,
        "MIN_FREE_DISK_MB": _positive_int(env, "MIN_FREE_DISK_MB", 512),
        "FFMPEG_PATH": env.get("FFMPEG_PATH", "ffmpeg"),
        "FFPROBE_PATH": env.get("FFPROBE_PATH", "ffprobe"),
        "TRANSCRIPTION_SEGMENT_MINUTES": _positive_int(
            env, "TRANSCRIPTION_SEGMENT_MINUTES", 30
        ),
        "TRANSCRIPTION_OVERLAP_SECONDS": _positive_int(
            env, "TRANSCRIPTION_OVERLAP_SECONDS", 5
        ),
        "SEGMENT_MAX_RETRIES": _positive_int(env, "SEGMENT_MAX_RETRIES", 3),
        "GEMINI_REQUEST_TIMEOUT_SECONDS": _positive_int(
            env, "GEMINI_REQUEST_TIMEOUT_SECONDS", 600
        ),
        "GEMINI_FILE_READY_TIMEOUT_SECONDS": _positive_int(
            env, "GEMINI_FILE_READY_TIMEOUT_SECONDS", 120
        ),
        "GEMINI_FILE_POLL_SECONDS": _positive_int(env, "GEMINI_FILE_POLL_SECONDS", 2),
        "AI_INPUT_TOKEN_BUDGET": _positive_int(env, "AI_INPUT_TOKEN_BUDGET", 700000),
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
