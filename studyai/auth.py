"""Account and session routes."""

from __future__ import annotations

import functools
import hashlib
import hmac
import re
import secrets
import smtplib
import sqlite3
from email.message import EmailMessage

from flask import (
    Blueprint,
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
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

auth_bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[\w.-]{3,30}$", re.UNICODE)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    g.user = (
        None
        if user_id is None
        else get_db()
        .execute(
            "SELECT id, name, username, email, is_admin, is_active FROM users WHERE id = ?",
            (user_id,),
        )
        .fetchone()
    )
    if g.user is not None and not g.user["is_active"]:
        session.clear()
        g.user = None


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
                    """INSERT INTO users
                       (name, username, email, password_hash, is_admin)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        name,
                        username,
                        email,
                        generate_password_hash(password),
                        int(email in current_app.config["ADMIN_EMAILS"]),
                    ),
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
        identifier = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = (
            get_db()
            .execute(
                "SELECT * FROM users WHERE username = ? OR email = ?",
                (identifier, identifier.lower()),
            )
            .fetchone()
        )
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("اسم المستخدم أو كلمة المرور غير صحيحة.", "error")
        elif not user["is_active"]:
            flash("هذا الحساب موقوف. تواصل مع إدارة الموقع.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("auth.dashboard"))
    return render_template("login.html")


@auth_bp.route("/forgot-password", methods=("GET", "POST"))
def forgot_password():
    if g.user:
        return redirect(url_for("auth.dashboard"))
    reset_preview_url = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is not None:
            reset_url = url_for(
                "auth.reset_password", token=_make_reset_token(user), _external=True
            )
            try:
                _deliver_reset_email(user["email"], user["name"], reset_url)
            except Exception:
                current_app.logger.exception("Could not deliver password reset email")
            else:
                if (
                    current_app.config["ENVIRONMENT"] == "development"
                    and current_app.config["MAIL_DELIVERY_MODE"] == "console"
                ):
                    reset_preview_url = reset_url
        flash("إذا كان البريد مسجلًا فستصلك رسالة تحتوي على رابط الاستعادة.", "success")
    return render_template("forgot_password.html", reset_preview_url=reset_preview_url)


@auth_bp.route("/reset-password/<token>", methods=("GET", "POST"))
def reset_password(token: str):
    if g.user:
        return redirect(url_for("auth.dashboard"))
    user = _user_from_reset_token(token)
    if user is None:
        flash("رابط الاستعادة غير صالح أو انتهت صلاحيته. اطلب رابطًا جديدًا.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirmation = request.form.get("password_confirmation", "")
        if len(password) < 8 or len(password) > 128:
            flash("كلمة المرور يجب أن تكون بين 8 و128 حرفًا.", "error")
        elif password != confirmation:
            flash("كلمتا المرور غير متطابقتين.", "error")
        else:
            database = get_db()
            database.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(password), user["id"]),
            )
            database.commit()
            session.clear()
            flash("تم تحديث كلمة المرور. يمكنك تسجيل الدخول الآن.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset_password.html")


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


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset-v1")


def _password_marker(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:24]


def _make_reset_token(user) -> str:
    return _reset_serializer().dumps(
        {"user_id": user["id"], "password_marker": _password_marker(user["password_hash"])}
    )


def _user_from_reset_token(token: str):
    try:
        payload = _reset_serializer().loads(
            token, max_age=current_app.config["PASSWORD_RESET_MAX_AGE_SECONDS"]
        )
        user_id = int(payload["user_id"])
        marker = str(payload["password_marker"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or not hmac.compare_digest(marker, _password_marker(user["password_hash"])):
        return None
    return user


def _deliver_reset_email(address: str, name: str, reset_url: str) -> None:
    if current_app.config["MAIL_DELIVERY_MODE"] == "console":
        current_app.logger.info("Password reset link for %s: %s", address, reset_url)
        return
    if current_app.config["MAIL_DELIVERY_MODE"] != "smtp":
        raise RuntimeError("MAIL_DELIVERY_MODE must be either console or smtp")
    if not current_app.config["SMTP_HOST"]:
        raise RuntimeError("SMTP_HOST is required when MAIL_DELIVERY_MODE=smtp")
    message = EmailMessage()
    message["Subject"] = "استعادة كلمة المرور — StudyAI"
    message["From"] = current_app.config["MAIL_FROM"]
    message["To"] = address
    message.set_content(
        f"مرحبًا {name}،\n\n"
        "استخدم الرابط التالي لاختيار كلمة مرور جديدة:\n"
        f"{reset_url}\n\n"
        "ستنتهي صلاحية الرابط خلال ساعة. إذا لم تطلب ذلك فتجاهل الرسالة."
    )
    smtp_class = smtplib.SMTP_SSL if current_app.config["SMTP_USE_SSL"] else smtplib.SMTP
    with smtp_class(
        current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"], timeout=15
    ) as smtp:
        if current_app.config["SMTP_USE_TLS"] and not current_app.config["SMTP_USE_SSL"]:
            smtp.starttls()
        if current_app.config["SMTP_USERNAME"]:
            smtp.login(current_app.config["SMTP_USERNAME"], current_app.config["SMTP_PASSWORD"])
        smtp.send_message(message)
