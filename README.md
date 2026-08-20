# StudyAI

StudyAI is an Arabic-first assistant with one focused workflow:

```text
Upload → Full transcript → Full summary → Questions
```

It is designed so multi-hour audio/video lectures do not depend on one browser request and are never
silently treated as complete when a segment is missing.

## Architecture

```text
Browser (chunked upload + status polling)
        │
        ▼
Flask blueprints ──► SQLite (users, uploads, jobs, segments, results)
        │
        ▼
Redis / RQ ──► dedicated worker
                    │
                    ├─► FFprobe validation
                    ├─► FFmpeg video/audio → mono 16 kHz FLAC
                    ├─► 30-minute segments with 5-second overlap
                    ├─► Gemini transcription, one recoverable segment at a time
                    ├─► strict ordered completeness check and overlap removal
                    └─► token-aware full summary and full-coverage questions
```

Flask, Jinja, vanilla JavaScript, and SQLite remain intentionally: the product is a focused modular
monolith and does not need a SPA, ORM, or microservices. Redis stores delivery state; SQLite is the
durable source of truth. RQ is smaller than Celery and supplies `SpawnWorker` for Windows.

## Reliability guarantees

- Browser lifetime and processing lifetime are independent.
- Uploads use server-generated 128-bit IDs and configurable chunks (8 MB by default).
- Duplicate identical chunks are idempotent; conflicting duplicates are rejected.
- The default maximum lecture upload is 5 GB with disk-space reserve checks.
- Video is converted to speech-focused lossless FLAC rather than sent to Gemini as video.
- Completed segment transcripts survive retries and worker restarts.
- A transcript can be assembled only when indexes are exactly `0..N-1`, all successful and non-empty.
- Summary/questions use the complete transcript when it fits; otherwise every segment contributes to a
  hierarchical reduction chosen with Gemini token counting. No prefix slicing is used.
- Successful jobs delete local original/intermediate files. Failed jobs retain them for retry; operators
  should remove expired data according to their retention policy.

## Prerequisites

- Python 3.12+
- Redis 6+ (not exposed publicly)
- FFmpeg and ffprobe available on `PATH`, or configured by absolute path
- Gemini API key

Windows: install Redis through Docker Desktop, WSL2, or the officially supported Windows partner, and
install FFmpeg from a trusted distributor. RQ automatically uses `SpawnWorker` through `worker.py`.
Linux production: install Redis and FFmpeg from the operating-system package repository; the normal RQ
worker is selected automatically.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy with `cp .env.example .env`.
Set `SECRET_KEY` to a long random value and set `GEMINI_API_KEY`. Never commit `.env`.

Important settings:

| Setting | Default | Purpose |
|---|---:|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Queue connection |
| `MAX_UPLOAD_GB` | `5` | Maximum complete lecture size |
| `UPLOAD_CHUNK_MB` | `8` | Independent browser chunk size |
| `MIN_FREE_DISK_MB` | `512` | Reserved free disk space |
| `TRANSCRIPTION_SEGMENT_MINUTES` | `30` | Retry/recovery unit |
| `TRANSCRIPTION_OVERLAP_SECONDS` | `5` | Boundary speech protection |
| `SEGMENT_MAX_RETRIES` | `3` | Attempts per segment |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Configurable stable multimodal model |
| `AI_INPUT_TOKEN_BUDGET` | `700000` | Safe threshold before hierarchy |

See `.env.example` for timeouts, storage paths, FFmpeg paths, and retention settings.

## Run locally

Start Redis, then use separate terminals:

```powershell
flask --app app run --debug
python worker.py
```

Open `http://127.0.0.1:5000`. On Linux production, set `APP_ENV=production`, use HTTPS, persist
`instance/`, and serve `app:app` with Gunicorn behind a reverse proxy. Run `python worker.py` as a
separately supervised service. Configure request/body limits to permit one upload chunk, not 5 GB.

## Operations and checks

```powershell
pytest
ruff check .
python -m compileall -q app.py worker.py studyai tests
python -m pip check
python -m pip_audit -r requirements.txt
python -m scripts.gemini_smoke
flask --app app cleanup-storage
```

- `/health` checks the application and SQLite.
- `/health/dependencies` reports only booleans for Redis/FFmpeg/ffprobe; it never calls Gemini.
- Apply public login/upload rate limiting at the reverse proxy. The app still enforces authentication,
  CSRF, ownership, chunk count/size, total size, media validation, and disk reserve.
- Run `cleanup-storage` periodically. Incomplete uploads older than 24 hours and retained failed-job media
  older than 7 days are removed by default; database audit records remain intact.

## Data and media lifecycle

SQLite is initialized additively and preserves the original `users` table. Upload chunks are deleted after
deterministic assembly. The assembled original, normalized audio, and segments live under a server-generated
upload directory. They are removed after success. Gemini remote files are explicitly deleted after each
segment (the provider also expires them after 48 hours). Cleanup errors are logged without changing a
successfully completed job into a failed one.

## Tests and limitations

Automated tests use tiny deterministic media/service doubles; they do not upload a three-hour recording or
consume Gemini credit. `scripts.gemini_smoke` is the explicit opt-in live provider check and consumes a small
amount of API quota. Actual throughput depends on Redis, FFmpeg, network, quota, and the configured model.
Processing is sequential by design to favor completeness and predictable quota use. SQLite is suitable for a
small single-site deployment; revisit the database only if measured concurrent write load requires it.

See `PROJECT_AUDIT.md` for the independent audit, feature regression inventory, verification categories,
security findings, and remaining technical debt.
