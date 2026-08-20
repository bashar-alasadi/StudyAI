"""Central application configuration."""

from __future__ import annotations

import os
from pathlib import Path


class Config:
    DEVELOPMENT_SECRET = "development-only-change-me"
    ENVIRONMENT = os.getenv("APP_ENV", "development").lower()
    SECRET_KEY = os.getenv("SECRET_KEY", DEVELOPMENT_SECRET)
    DATABASE = os.getenv("DATABASE_PATH", str(Path("instance") / "studyai.sqlite3"))
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENVIRONMENT == "production"
