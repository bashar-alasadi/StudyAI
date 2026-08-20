"""StudyAI application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from . import db
from .api import api_bp
from .auth import auth_bp, load_logged_in_user
from .config import Config
from .csrf import ensure_csrf_token


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _validate_config(app)
    _configure_logging(app)

    db.init_app(app)
    app.before_request(load_logged_in_user)
    app.before_request(ensure_csrf_token)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

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


def _validate_config(app: Flask) -> None:
    if app.config["TESTING"]:
        return
    secret = app.config.get("SECRET_KEY", "")
    if app.config["ENVIRONMENT"] == "production" and (
        not secret or secret == Config.DEVELOPMENT_SECRET
    ):
        raise RuntimeError("SECRET_KEY must be set to a strong value in production")


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
