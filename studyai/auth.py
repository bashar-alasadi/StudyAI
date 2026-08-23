"""Public access and administrator-only authentication."""

from __future__ import annotations

import functools
import secrets

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from .db import get_db

auth_bp = Blueprint("auth", __name__)
PUBLIC_USERNAME = "__public__"


def public_user_id() -> int:
    row = get_db().execute(
        "SELECT id FROM users WHERE username = ?", (PUBLIC_USERNAME,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Public workspace account is missing")
    return int(row["id"])


def remember_resource(kind: str, resource_id: str) -> None:
    key = f"public_{kind}_ids"
    values = [value for value in session.get(key, []) if value != resource_id]
    session[key] = (values + [resource_id])[-20:]


def owns_resource(kind: str, resource_id: str) -> bool:
    return bool(session.get("admin_authenticated")) or resource_id in session.get(
        f"public_{kind}_ids", []
    )


def load_logged_in_user() -> None:
    """Load only the administrator session; students never need an account."""
    if session.get("admin_authenticated"):
        g.user = {
            "id": public_user_id(),
            "name": "مدير الموقع",
            "username": current_app.config["ADMIN_USERNAME"],
            "email": "",
            "is_admin": 1,
            "is_active": 1,
        }
    else:
        g.user = None


def login_required(view):
    """Compatibility decorator: the study workspace is intentionally public."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        return view(**kwargs)

    return wrapped


@auth_bp.get("/register")
def register():
    abort(404)


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    return redirect(url_for("auth.admin_login"))


@auth_bp.route("/admin/login", methods=("GET", "POST"))
def admin_login():
    if session.get("admin_authenticated"):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        configured_username = current_app.config["ADMIN_USERNAME"]
        configured_hash = current_app.config["ADMIN_PASSWORD_HASH"]
        configured_password = current_app.config["ADMIN_PASSWORD"]
        valid_password = (
            check_password_hash(configured_hash, password)
            if configured_hash
            else secrets.compare_digest(configured_password, password)
        )
        if secrets.compare_digest(username, configured_username) and valid_password:
            session.clear()
            session.permanent = True
            session["admin_authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin.dashboard"))
        flash("بيانات دخول الإدارة غير صحيحة.", "error")
    return render_template("login.html", admin_only=True)


@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@auth_bp.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


def admin_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if not session.get("admin_authenticated"):
            if request.path.startswith("/api/"):
                return jsonify(error="هذه الصفحة خاصة بإدارة الموقع."), 401
            return redirect(url_for("auth.admin_login"))
        return view(**kwargs)

    return wrapped
