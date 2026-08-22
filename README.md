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
- Public YouTube links and direct audio/video URLs can enter the same transcription,
  summary, and question pipeline. Playlists, private videos, and private-network URLs are rejected.
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

Windows: install Redis through Docker Desktop, WSL2, or Memurai, the Windows partner linked by Redis, and
install FFmpeg from a Windows build linked by ffmpeg.org. `worker.py` uses StudyAI's Windows-safe
`WindowsSpawnWorker`, including orphaned-job recovery after a forced worker stop.
Linux production: install Redis and FFmpeg from the operating-system package repository; the normal RQ
worker is selected automatically.

### Verified native Windows development setup

Phase 3 was accepted on Windows 10 x64 without administrator privileges using:

- FFmpeg 9.0.1 essentials from Gyan's build linked by ffmpeg.org, extracted below
  `$env:LOCALAPPDATA\StudyAI\tools\ffmpeg-verified` after SHA-256 verification.
- Signed Memurai Developer 4.1.7 executables from the official `MemuraiDeveloper` NuGet package, extracted
  below `$env:LOCALAPPDATA\StudyAI\tools\memurai-4.1.7`.

Set explicit media paths in each application/worker terminal when FFmpeg is not on `PATH`:

```powershell
$ffmpegBin = Join-Path $env:LOCALAPPDATA `
  'StudyAI\tools\ffmpeg-verified\ffmpeg-9.0.1-essentials_build\bin'
$env:FFMPEG_PATH = Join-Path $ffmpegBin 'ffmpeg.exe'
$env:FFPROBE_PATH = Join-Path $ffmpegBin 'ffprobe.exe'
```

The following terminal-bound Memurai command was verified for local development/acceptance. It intentionally
disables persistence and is not a production service definition:

```powershell
$memurai = Join-Path $env:LOCALAPPDATA 'StudyAI\tools\memurai-4.1.7\tools\memurai.exe'
& $memurai --port 6379 --save '""' --appendonly no
```

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
| `MIN_FREE_DISK_MB` | `64` | Reserved free disk space after each assembly step |
| `WEB_DOWNLOAD_TIMEOUT_SECONDS` | `30` | Timeout for each web-media network request |
| `SESSION_LIFETIME_DAYS` | `30` | Signed-in session lifetime |
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

## Deploy from GitHub

The repository includes a production `Dockerfile`. The container runs Gunicorn and the RQ worker
together so both processes share SQLite and uploaded media. Configure the hosting service as follows:

- Build method: Dockerfile (no custom build/start command).
- Health check: `/health`.
- Container port: use the platform-provided `PORT`; the default is `8000`.
- Persistent volume: mount at `/app/instance`. Without it, accounts and jobs are lost on redeploy.
- Redis: create a private managed Redis service and set its internal URL as `REDIS_URL`.
- HTTPS: enable the hosting platform's managed HTTPS/proxy.

Required production environment variables:

```dotenv
APP_ENV=production
SECRET_KEY=generate-a-long-random-secret
ADMIN_EMAILS=owner@your-domain.example
GEMINI_API_KEY=your-google-ai-api-key
REDIS_URL=redis://your-private-redis:6379/0
DATABASE_PATH=/app/instance/studyai.sqlite3
UPLOAD_ROOT=/app/instance/uploads
MAIL_DELIVERY_MODE=smtp
MAIL_FROM=StudyAI <no-reply@your-domain.example>
SMTP_HOST=your-smtp-host
SMTP_PORT=587
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-app-password
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

The deployment must provide enough persistent disk for the configured `MAX_UPLOAD_GB`. Never expose
Redis publicly or commit real secrets. `docker compose up --build` reproduces the production topology
locally when Docker is available.

### Administration

Set `ADMIN_EMAILS` to the owner's email before registering. Matching accounts receive administrator
access automatically and see the `/admin/` dashboard. For an existing account, run:

```powershell
flask --app app promote-admin owner@example.com
```

Administrators can review site statistics and processing jobs, suspend/reactivate users, grant/revoke
administrator access, and configure the active Gemini model and API key. Provider keys are encrypted at
rest using the application's `SECRET_KEY`; changing that secret invalidates saved provider keys.

## Password reset email

Password-reset links expire after one hour and become invalid immediately after the password is changed.
Local development uses `MAIL_DELIVERY_MODE=console`, which exposes a test link on the reset page.
For production, configure an SMTP account in `.env`:

```dotenv
MAIL_DELIVERY_MODE=smtp
MAIL_FROM=StudyAI <no-reply@example.com>
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=no-reply@example.com
SMTP_PASSWORD=replace-with-an-app-password
SMTP_USE_TLS=true
SMTP_USE_SSL=false
PASSWORD_RESET_MAX_AGE_SECONDS=3600
```

## Operations and checks

```powershell
pytest
ruff check .
python -m compileall -q app.py worker.py studyai tests
python -m pip check
python -m pip_audit -r requirements.txt
python -m scripts.gemini_smoke
python -m scripts.native_media_smoke --ffmpeg $env:FFMPEG_PATH --ffprobe $env:FFPROBE_PATH
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
