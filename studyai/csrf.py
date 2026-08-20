"""Session-backed CSRF protection for state-changing requests."""

from __future__ import annotations

import hmac
import secrets

from flask import abort, request, session


def ensure_csrf_token() -> None:
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(token, submitted):
            abort(400, description="رمز الحماية غير صالح. حدّث الصفحة وحاول مجددًا.")
