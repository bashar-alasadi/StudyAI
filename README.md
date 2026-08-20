# StudyAI

StudyAI is an Arabic-first study assistant that turns recorded lectures into readable transcripts,
structured summaries, and review questions. It is a compact Flask application intended for students.

## Features

- Secure local accounts with hashed passwords and server-side validation.
- Upload common audio/video formats and transcribe them with Gemini.
- Generate an Arabic summary and review questions from the transcript.
- Responsive Arabic interface, explicit loading/error states, and copy-to-clipboard support.
- CSRF protection, upload-size limits, safe filenames, sanitized errors, and a health endpoint.

## Architecture

```text
app.py                     WSGI entry point
studyai/
  __init__.py              application factory and error handling
  auth.py                  account/session presentation layer
  api.py                   authenticated JSON endpoints and validation
  db.py                    SQLite data access and schema initialization
  config.py                environment configuration
  csrf.py                  request forgery protection
  services/ai.py           Gemini integration
  templates/               server-rendered pages
  static/                  CSS and browser JavaScript
tests/                     route, authentication, security, and API tests
```

SQLite is deliberately used instead of an ORM: the current data model has one table and does not
justify another dependency. The Gemini client is isolated behind a service so tests never call the
external API and a future provider can be substituted without changing routes or UI code.

## Setup

Prerequisites: Python 3.12+ and a Gemini API key.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
copy .env.example .env  # use: cp .env.example .env on macOS/Linux
```

Set a long random `SECRET_KEY` and your `GEMINI_API_KEY` in `.env`. The configured default model is
`gemini-3.6-flash`; override it with `GEMINI_MODEL` when needed. Real secrets must never be committed.

## Development

```bash
flask --app app run --debug
pytest
ruff check .
```

The database is initialized automatically in `instance/studyai.sqlite3`. To recreate an empty
database, remove it only when no user data must be preserved, then run `flask --app app init-db`.

## Production

Set `APP_ENV=production`, configure a strong `SECRET_KEY`, and serve the WSGI object `app:app` behind
HTTPS. Example on Linux:

```bash
gunicorn --workers 2 --bind 0.0.0.0:8000 app:app
```

Persist the `instance` directory, restrict access to `.env`, terminate TLS at the proxy, and choose
request timeouts suitable for long audio processing. For multiple instances or substantial usage,
move AI work to a background queue and replace SQLite with a managed relational database.

## API

Browser requests are authenticated by the session cookie and require the CSRF token emitted in the
page. Endpoints: `POST /api/transcriptions`, `POST /api/summaries`, `POST /api/questions`, and
`GET /health`. The API is designed for the included same-origin UI, not as a public third-party API.

## Known limitations

- Transcription is synchronous; very long files can exceed proxy or platform timeouts.
- Lecture content is not persisted, so refreshing the dashboard clears the current work.
- SQLite and local sessions target a single small deployment, not horizontal scaling.
- Live Gemini verification requires a valid API key and network access and is not part of automated tests.

See `PROJECT_AUDIT.md` for the modernization record, decisions, validation status, and remaining debt.
