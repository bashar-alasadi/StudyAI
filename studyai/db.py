"""Small SQLite persistence layer for user accounts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    model TEXT NOT NULL,
    api_key_encrypted TEXT,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    upload_id TEXT,
    source_url TEXT,
    status TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    media_type TEXT,
    original_size INTEGER NOT NULL DEFAULT 0 CHECK (original_size >= 0),
    duration_seconds REAL,
    total_segments INTEGER NOT NULL DEFAULT 0 CHECK (total_segments >= 0),
    completed_segments INTEGER NOT NULL DEFAULT 0 CHECK (completed_segments >= 0),
    current_stage TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    transcript TEXT,
    summary TEXT,
    questions TEXT,
    explanation TEXT,
    include_explanations INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    safe_error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_processing_jobs_user_created
ON processing_jobs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS transcription_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES processing_jobs(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL CHECK (segment_index >= 0),
    start_seconds REAL NOT NULL CHECK (start_seconds >= 0),
    end_seconds REAL NOT NULL CHECK (end_seconds > start_seconds),
    status TEXT NOT NULL,
    transcript TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(job_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_segments_job_status
ON transcription_segments(job_id, status);

CREATE TABLE IF NOT EXISTS upload_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    total_size INTEGER NOT NULL CHECK (total_size > 0),
    chunk_size INTEGER NOT NULL CHECK (chunk_size > 0),
    expected_chunks INTEGER NOT NULL CHECK (expected_chunks > 0),
    received_chunks INTEGER NOT NULL DEFAULT 0 CHECK (received_chunks >= 0),
    status TEXT NOT NULL,
    assembled_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_user_created
ON upload_sessions(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS upload_chunks (
    upload_id TEXT NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    size INTEGER NOT NULL CHECK (size > 0),
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(upload_id, chunk_index)
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = Path(current_app.config["DATABASE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    database = get_db()
    database.executescript(SCHEMA)
    columns = {row["name"] for row in database.execute("PRAGMA table_info(users)")}
    if "is_admin" not in columns:
        database.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "is_active" not in columns:
        database.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    job_columns = {
        row["name"] for row in database.execute("PRAGMA table_info(processing_jobs)")
    }
    if "source_url" not in job_columns:
        database.execute("ALTER TABLE processing_jobs ADD COLUMN source_url TEXT")
    if "explanation" not in job_columns:
        database.execute("ALTER TABLE processing_jobs ADD COLUMN explanation TEXT")
    if "include_explanations" not in job_columns:
        database.execute(
            "ALTER TABLE processing_jobs ADD COLUMN include_explanations "
            "INTEGER NOT NULL DEFAULT 0"
        )
    database.execute(
        """INSERT OR IGNORE INTO users
           (name, username, email, password_hash, is_admin, is_active)
           VALUES (?, ?, ?, ?, 0, 1)""",
        ("الزوار", "__public__", "public@studyai.local", "!"),
    )
    database.commit()


@click.command("init-db")
def init_db_command() -> None:
    init_db()
    click.echo("Database initialized.")


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    with app.app_context():
        init_db()
