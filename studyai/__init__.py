"""StudyAI application factory."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db
from .admin import admin_bp, promote_admin_command
from .api import api_bp
from .auth import auth_bp, load_logged_in_user
from .config import DEVELOPMENT_SECRET, build_config
from .csrf import ensure_csrf_token
from .queueing import init_queue
from .uploads import uploads_bp


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(build_config())
    if test_config:
        app.config.update(test_config)
    if app.config["ENVIRONMENT"] == "production":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _validate_config(app)
    _configure_logging(app)

    db.init_app(app)
    init_queue(app)
    app.before_request(load_logged_in_user)
    app.before_request(ensure_csrf_token)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(uploads_bp)
    app.cli.add_command(promote_admin_command)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        try:
            db.get_db().execute("SELECT 1").fetchone()
        except Exception:
            return jsonify(status="unhealthy"), 503
        return jsonify(status="ok")

    @app.get("/health/dependencies")
    def dependency_health():
        redis_ok = False
        try:
            from redis import Redis

            redis_ok = bool(
                Redis.from_url(
                    app.config["REDIS_URL"], socket_connect_timeout=1, socket_timeout=1
                ).ping()
            )
        except Exception:
            pass
        direct_media = app.config["DIRECT_MEDIA_PROCESSING"]
        local_queue = app.config["JOB_QUEUE_MODE"] in {"thread", "sync"}
        ffmpeg_ok = direct_media or _command_available(app.config["FFMPEG_PATH"])
        ffprobe_ok = direct_media or _command_available(app.config["FFPROBE_PATH"])
        redis_ok = local_queue or redis_ok
        payload = {
            "application": True,
            "database": True,
            "ai": bool(app.config["GEMINI_API_KEY"]),
            "redis": redis_ok,
            "ffmpeg": ffmpeg_ok,
            "ffprobe": ffprobe_ok,
        }
        return jsonify(payload), 200 if all(payload.values()) else 503

    @app.cli.command("cleanup-storage")
    @click.option("--stale-hours", type=int, default=None)
    @click.option("--failed-hours", type=int, default=None)
    def cleanup_storage(stale_hours, failed_hours):
        from .services.uploads import cleanup_expired_job_media, cleanup_stale_uploads

        hours = stale_hours or app.config["STALE_UPLOAD_HOURS"]
        retention = failed_hours or app.config["FAILED_MEDIA_RETENTION_HOURS"]
        stale_removed = cleanup_stale_uploads(hours)
        media_removed = cleanup_expired_job_media(retention)
        click.echo(
            f"Removed {stale_removed} stale upload session(s) and "
            f"{media_removed} expired failed-job media directorie(s)."
        )

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        return response

    @app.errorhandler(Exception)
    def handle_error(error: Exception):
        if isinstance(error, HTTPException):
            status = error.code or 500
            message = error.description
        else:
            status = 500
            message = "حدث خطأ داخلي غير متوقع"
            app.logger.exception("Unhandled request error")
        if request.path.startswith("/api/"):
            return jsonify(error=message), status
        return render_template("error.html", status=status, message=message), status

    return app


def _command_available(command: str) -> bool:
    path = Path(command)
    return path.is_file() if path.is_absolute() else shutil.which(command) is not None


def _validate_config(app: Flask) -> None:
    if app.config["TESTING"]:
        return
    secret = app.config.get("SECRET_KEY", "")
    if app.config["ENVIRONMENT"] == "production" and (not secret or secret == DEVELOPMENT_SECRET):
        raise RuntimeError("SECRET_KEY must be set to a strong value in production")
    if app.config["ENVIRONMENT"] == "production" and not (
        app.config["ADMIN_PASSWORD"] or app.config["ADMIN_PASSWORD_HASH"]
    ):
        raise RuntimeError("ADMIN_PASSWORD or ADMIN_PASSWORD_HASH must be set in production")


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
