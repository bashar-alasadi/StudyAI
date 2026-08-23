"""Encrypted, database-backed AI provider configuration."""

from __future__ import annotations

import base64
import hashlib

from flask import current_app, has_app_context

from .db import get_db


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(value: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("AI provider key cannot be decrypted") from error


def active_provider() -> dict[str, str] | None:
    row = (
        get_db()
        .execute(
            """SELECT provider_type, model, api_key_encrypted FROM ai_providers
           WHERE is_active = 1 ORDER BY id LIMIT 1"""
        )
        .fetchone()
    )
    if row is None:
        return None
    return {
        "provider_type": row["provider_type"],
        "model": row["model"],
        "api_key": decrypt_api_key(row["api_key_encrypted"]),
    }


def resolve_ai_config(config) -> dict:
    resolved = dict(config)
    if not has_app_context():
        return resolved
    provider = active_provider()
    if provider:
        resolved["AI_PROVIDER"] = provider["provider_type"]
        if provider["provider_type"] == "gemini":
            resolved["GEMINI_API_KEY"] = provider["api_key"]
            resolved["GEMINI_MODEL"] = provider["model"]
        elif provider["provider_type"] == "openai":
            resolved["OPENAI_API_KEY"] = provider["api_key"]
            resolved["OPENAI_MODEL"] = provider["model"]
    return resolved


def _fernet():
    # Import lazily so platforms without a compatible cryptography binary can
    # still start the web application. Encryption is loaded only when needed.
    from cryptography.fernet import Fernet

    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)
