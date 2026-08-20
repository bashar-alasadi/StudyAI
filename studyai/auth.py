"""Account and session routes."""

from __future__ import annotations

import functools
import re
import secrets
import sqlite3

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

auth_bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[\w.-]{3,30}$", re.UNICODE)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    g.user = None if user_id is None else get_db().execute(
        "SELECT id, name, username, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="يجب تسجيل الدخول أولًا."), 401
            flash("سجّل الدخول للوصول إلى لوحة الدراسة.", "info")
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped


@auth_bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("auth.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = _validate_registration(name, username, email, password)
        if error is None:
            try:
                database = get_db()
                database.execute(
                    "INSERT INTO users (name, username, email, password_hash) VALUES (?, ?, ?, ?)",
                    (name, username, email, generate_password_hash(password)),
                )
                database.commit()
            except sqlite3.IntegrityError:
                error = "اسم المستخدم أو البريد الإلكتروني مستخدم بالفعل."
            else:
                flash("تم إنشاء الحساب. يمكنك تسجيل الدخول الآن.", "success")
                return redirect(url_for("auth.login"))
        flash(error, "error")
    return render_template("register.html")


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("auth.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("اسم المستخدم أو كلمة المرور غير صحيحة.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("auth.dashboard"))
    return render_template("login.html")


@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@auth_bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


def _validate_registration(name: str, username: str, email: str, password: str) -> str | None:
    if not name or len(name) > 80:
        return "أدخل اسمًا صحيحًا لا يتجاوز 80 حرفًا."
    if not USERNAME_RE.fullmatch(username):
        return "اسم المستخدم يجب أن يكون بين 3 و30 حرفًا دون مسافات."
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        return "أدخل بريدًا إلكترونيًا صحيحًا."
    if len(password) < 8 or len(password) > 128:
        return "كلمة المرور يجب أن تكون بين 8 و128 حرفًا."
    return None
