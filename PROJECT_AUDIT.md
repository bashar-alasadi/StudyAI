# StudyAI Modernization Audit

Last updated: 2026-08-21

## Baseline and inferred purpose

The original repository was a seven-file learning project: a single Flask module, four standalone HTML
pages, one global stylesheet, and an unpinned requirements file. Its intended user is an Arabic-speaking
student who uploads a recorded lecture, receives a transcript, then creates a summary and review questions.
Only transcription had a backend implementation. Summary/questions were alerts, root returned plain text,
the homepage upload did nothing, and page links did not match Flask routes.

Baseline verification found Python absent from the default PATH. The bundled Python 3.12.13 compiled the
single module but had no project dependencies installed; import failed on Flask. Test discovery found zero
tests. The existing `.env` was correctly ignored and contains a Gemini key (value was not printed).

## Primary findings

### P0/P1

- Passwords were stored in browser `localStorage` as plain text; there was no real authentication.
- Debug mode was enabled in the executable server and raw provider exceptions were returned to users.
- Summary and question generation, two core promised features, were unfinished.
- All backend, configuration, provider calls, validation, and cleanup lived in one route module.
- No CSRF protection, upload size limit, environment validation, tests, or production guidance.
- The Gemini model was hardcoded. The provider upload was never deleted remotely.

### P2/P3

- Invalid HTML, inline styles/scripts, broken links, inaccessible status flows, and weak mobile behavior.
- No dependency bounds, README, health endpoint, structured logs, or development tooling.
- Synchronous external AI work remains a scaling concern.

## Target architecture and decisions

The target is a layered modular monolith: route blueprints handle HTTP, a service module owns Gemini,
SQLite owns minimal account persistence, and templates/static assets form the presentation layer. This is
the smallest architecture that separates responsibilities and supports testing. Flask and vanilla browser
JavaScript remain appropriate; a SPA, ORM, task queue, or distributed services would currently add cost
without solving a demonstrated requirement.

The default model is configurable and set to `gemini-3.6-flash`, verified as a production-ready multimodal
model in the provider documentation on 2026-08-21. Uploaded provider files are explicitly deleted after use.

## Completed work

- Replaced standalone files with an application factory, blueprints, service layer, and centralized config.
- Implemented server-side registration/login/logout, hashed passwords, validation, and protected dashboard.
- Added session-backed CSRF validation and secure cookie defaults.
- Added real transcription, summary, and question APIs with safe errors and input limits.
- Added local and remote temporary-file cleanup, configurable model, and missing-key handling.
- Rebuilt the Arabic UI with responsive templates and explicit loading/error/result states.
- Added SQLite schema initialization, health endpoint, structured base logging, test doubles, and tests.
- Added bounded dependencies, lint/test configuration, `.env.example`, README, and deployment guidance.

## Validation status

- Pre-change: Python compilation passed; import failed because dependencies were absent; zero tests existed.
- Post-change: dependency installation completed in `.venv`; `pip check` found no conflicts.
- Post-change: Ruff passed with no findings; Python compilation and application import passed.
- Post-change: all 12 automated route, authentication, CSRF, API, and error-flow tests passed.
- Post-change: the Flask development server started successfully and exposed all expected routes.
- Post-change: browser inspection verified the Arabic homepage DOM, visual layout, and zero console errors.
- Not executed: a live Gemini request, because automated validation must not consume the real API key.

## Remaining limitations and roadmap

- P1: Verify a real Gemini request using a non-production sample and an authorized API key.
- P2: Move long transcription jobs to a queue if platform timeouts become a real usage problem.
- P2: Persist lecture projects/history only after retention and privacy requirements are agreed.
- P2: Add rate limiting at the reverse proxy or application layer before public deployment.
- P3: Add end-to-end browser tests when CI gains a browser runtime.

No destructive database migration was performed. The original project had no database or stored server data.

# Post-Modernization Independent Audit & Long-Lecture Architecture

## Fresh baseline and Phase 1 defects

The audit re-read the repository and history rather than trusting the previous report. Phase 1 had 12 tests,
clean Ruff/compilation, and no dependency conflicts, but its test run initially encountered an unrelated
Windows ACL failure in pytest's global temporary directory. Tests now use a repository-local temporary path.

Discovered defects:

- **Critical:** configuration values were evaluated when `Config` was imported, before `.env` loading. A
  fresh-process reproduction proved production, secret, Gemini key, and secure-cookie values remained stale.
- **Critical:** multi-hour work used one HTTP request and one provider request; refresh/timeouts lost work.
- **High:** synchronous summary/questions rejected text beyond 500,000 characters, contradicting full-source
  behavior; no token-aware path existed.
- **High:** upload trusted only an extension, was limited to one 100 MB request, and was not resumable.
- **High:** provider readiness, request timeout, bounded segment retry, resume, and client close were absent.
- **Medium:** local and remote cleanup failures were swallowed. A later audit also found successful-job cleanup
  could incorrectly change a completed job to failed; cleanup is now independently logged.
- **Medium:** CSRF was preserved through login rather than rotated.
- **Medium:** Phase 1 tests replaced the complete AI service and did not cover SDK boundaries, job ownership,
  missing segments, retries, hierarchical coverage, media corruption, or interrupted uploads.

## Feature regression inventory

| Original capability | Current behavior | Status / intentional change |
|---|---|---|
| Homepage | Arabic product explanation and navigation | Preserved and improved |
| Registration/login/logout | Server sessions, validation, hashed passwords, rotated CSRF | Replaced insecure localStorage intentionally |
| Dashboard | Persistent asynchronous workflow | Preserved and expanded |
| Lecture upload | Chunked, resumable, size/disk/ownership checks | Replaced single request intentionally |
| Transcription | All media segments required before assembly | Preserved with completeness guarantee |
| Summary | Full source or hierarchical full coverage | Completed (was placeholder) |
| Questions | Full source or hierarchical full coverage | Completed (was placeholder) |
| Copy | Copies selected transcript/summary/questions | Preserved and expanded |
| Arabic RTL | Jinja templates and responsive RTL UI | Preserved |
| Navigation | Flask route names and authenticated links | Fixed |

## Architecture decisions

Flask modular monolith, Jinja, vanilla JavaScript, and SQLite remain. RQ 2.x and Redis provide delivery while
SQLite persists uploads, jobs, segments, progress, retries, and results. `SpawnWorker` is selected on Windows;
the normal worker is selected on Linux. Queue internals are behind an adapter and tests run synchronously.

Uploads default to 8 MB chunks and a 5 GB total cap. FFprobe validates actual media; FFmpeg extracts mono
16 kHz lossless FLAC and creates 30-minute segments with 5-second overlap. These values are configurable.
Sequential Gemini calls limit quota pressure. Each segment retries at most three times and completed segments
are reused after delivery/process restarts.

The completeness invariant compares successful indexes with the persisted expected count exactly. Transcript
assembly cannot proceed with a failed, empty, missing, duplicated, or out-of-order segment. Deterministic word
overlap removal avoids sending content through another lossy AI step.

Summary and question generation calls provider token counting. The whole source is used when safe; otherwise
all segment groups produce intermediate material that is recursively reduced and globally synthesized. There
is no arbitrary prefix truncation.

## Security and lifecycle

Passwords use Werkzeug scrypt/PBKDF2-compatible hashing. Login rotates CSRF, logout and every mutation require
CSRF, cookies are HTTP-only/SameSite and secure in production, and public IDs are high entropy. Queries scope
uploads/jobs to the authenticated owner, preventing IDOR. Storage paths are generated by the server; subprocess
arguments are arrays with no shell interpolation. CSP, frame, MIME-sniffing, and referrer headers remain.

Chunks are removed after assembly. Original, normalized, and segment media are removed after success. Failed
media is retained for recovery and must be cleaned according to the documented retention policy. Remote Gemini
files are deleted in `finally`; deletion failures are safely logged. Reverse-proxy rate limiting remains a
deployment responsibility.

## Verification categories (2026-08-21)

- **Verified in code:** explicit state machine, ownership queries, no arbitrary transcript slicing, additive
  SQLite schema, configurable model/timeouts/segments/uploads.
- **Tested with mocks:** FFprobe/FFmpeg calls, corrupt media, three-segment processing, retry, restart/resume,
  missing-segment rejection, overlap removal, Gemini readiness/timeout/deletion/close, hierarchical coverage.
- **Tested locally:** 52 tests, Ruff, Python compilation, JavaScript syntax, `pip check`, and `pip-audit`
  (no known vulnerabilities at the recorded run).
- **Tested through the browser:** login, authenticated dashboard, persisted completed-job recovery after reload,
  100% progress, segment counts, result tabs, and clean browser console. The browser harness file chooser did not
  open, so chunk upload interaction remains covered by request-level integration tests rather than browser E2E.
- **Tested against Gemini:** a generated one-second, non-sensitive WAV was uploaded, processed by the configured
  `gemini-3.6-flash` model, and explicitly deleted successfully.
- **Not verified in this environment:** native FFmpeg/ffprobe and Redis connectivity/worker execution because
  neither dependency is installed/running on this workstation.

## Dependency findings

Direct compatibility bounds intentionally remain conservative. The environment reported newer major versions
of google-genai, Redis client, and pytest; they were not blindly upgraded during a reliability refactor. RQ
2.11.0 and Redis client 6.4.0 are installed and conflict-free. `pip-audit` found no known vulnerabilities.

## Remaining technical debt

- Schedule periodic cleanup in the deployment supervisor; the CLI removes both stale uploads and expired failed
  media, but no scheduler is bundled.
- A failed job retained beyond policy may lose retry media after operator cleanup; the UI should explain expiry.
- Rate limits are documented for the reverse proxy rather than implemented in-process.
- SQLite remains appropriate for the current single-site scope, not high-write horizontal scaling.
- Live provider behavior and throughput must be rechecked whenever the Gemini SDK/model major version changes.

## Final merge verdict

**NOT SAFE TO MERGE** into the production branch yet. The implementation and automated suite are complete, and
the real Gemini smoke check passed, but the acceptance environment still must demonstrate one native
FFmpeg/ffprobe conversion and one Redis-backed RQ worker job (including restart/resume). These are deployment
infrastructure gates, not known source-code failures.

# Phase 3 — Environment & Acceptance Validation

Validation date: 2026-08-21. This section supersedes the Phase 2 merge verdict above.

## Workstation

- OS: Microsoft Windows 10 Pro 10.0.19045, x64.
- Project runtime: Python 3.12.13 in `.venv`; pip 25.0.1.
- FFmpeg/ffprobe: 9.0.1 essentials build, SHA-256 matched the distributor value published through the
  Windows-build link on ffmpeg.org. Installed below per-user LocalAppData.
- Redis transport: signed Memurai Developer 4.1.7 executables from the official NuGet package; Redis API
  compatibility 7.2.11. No Windows service or firewall rule was installed.
- Python queue stack: RQ 2.11.0 and redis-py 6.4.0.
- Package managers: winget, Chocolatey, Scoop, and Docker were unavailable. WSL was not configured and its
  feature inspection required elevation, so neither unsupported Redis builds nor unrelated system changes
  were used.

## Infrastructure

- Native Memurai listened on `127.0.0.1:6379`; native CLI and redis-py both returned `PONG`/`True`.
- The StudyAI RQ adapter connected to queue `studyai`.
- `/health` returned 200 and `/health/dependencies` returned 200 with application, SQLite, Redis, FFmpeg, and
  ffprobe all `true`.
- The real Flask server and real Redis-backed RQ worker ran concurrently without fork errors after the
  Windows worker correction described below.

## Defects found and corrected

1. RQ 2.11 rejects `:` in job IDs. `studyai:<id>` prevented real queue delivery. IDs now use the valid,
   deterministic `studyai-<id>` form, and enqueue failures become persisted, retryable application failures.
2. The installed RQ `SpawnWorker` still called Unix-only `os.wait4`/process-group APIs and passed multiline
   child code through native Windows command parsing. `WindowsSpawnWorker` now launches a module entry point,
   waits with `os.waitpid`, and terminates only its exact child PID.
3. A killed worker left its RQ execution abandoned while SQLite correctly retained segment progress. Worker
   startup now cleans registries, identifies only nonterminal jobs with no active RQ execution, returns them
   to `queued`, and re-enqueues them without deleting completed segments.

Regression tests cover the valid RQ ID, queue-send failure persistence, Windows wait behavior, and preservation
of completed segment counters during interrupted resume.

## Real Pipeline

- Generated input: non-sensitive 125-second, 16 kHz mono WAV with an Arabic filename; 4,000,078 bytes.
- Normal HTTP path created a four-chunk upload with 1 MiB chunks. Two chunks were sent, chunk 0 was repeated
  idempotently, premature finalization returned 409, and the remaining chunks resumed successfully.
- Flask enqueued the job in Redis; the native Windows RQ worker consumed it.
- StudyAI invoked native ffprobe, normalized with native FFmpeg, and created three one-minute test segments
  with five-second overlap. Production's 30-minute default was not changed.
- All three segments reached Gemini through the RQ worker. Persisted indexes were exactly `[0, 1, 2]`, all
  completed, with retry count zero for this successful fixture.
- Final state was `completed`, progress 100, and `completed_segments == total_segments == 3`. Transcript,
  summary, and questions were non-empty (269, 292, and 1,197 characters respectively).
- The generated tone contained no speech; Gemini correctly described that fact. No private lecture content was
  used or recorded.

## Recovery

- The real worker was forcibly stopped after at least two segment transcripts were durable. The database was
  not modified or corrupted.
- On restart, RQ moved the abandoned execution to its failed registry; StudyAI re-enqueued the nonterminal job.
  Worker logs showed no repeated media upload/transcription calls during the resumed run and proceeded from
  saved segments to token counting, summary, and questions.
- Browser login and dashboard reload restored 100%, `3 of 3`, and all result tabs. Refresh created no duplicate
  job; the acceptance user retained exactly one processing job.
- Browser copy changed to `تم النسخ ✓`; browser warnings/errors were empty.
- The browser automation file chooser still did not expose a selectable event for the hidden input. Therefore
  a full chooser-driven browser upload is not claimed; the same real HTTP upload endpoints, Redis worker,
  browser recovery, tabs, RTL UI, and copy behavior were independently exercised.

## Completeness and full-source generation

- Controlled automated tests prove a missing/empty/failed/out-of-order segment blocks assembly and that adding
  the missing successful segment permits it.
- Retry tests prove transient segment failures increment persisted retry state and eventually complete, while
  exhausted/non-retryable failures do not produce a final transcript.
- Direct and forced hierarchical tests prove summary and questions consume the full source and that every late
  group contributes; no arbitrary prefix truncation exists.

## Cleanup

- Successful real processing removed the upload directory containing the assembled source, normalized audio,
  and segments.
- The live Gemini smoke script uploaded, generated, and explicitly deleted its provider file successfully.
- Two controlled failed-job directories were retained initially, aged to eight days, then removed by the real
  `cleanup-storage` command. Their database audit rows remained and `assembled_path` became null.
- The native media smoke used an isolated temporary directory, an Arabic filename, real inspection,
  normalization, and three ordered overlapping segments; its temporary directory was removed on exit.

## Tests

- pytest: 56 passed.
- Ruff: passed.
- Python compilation (`app.py`, `worker.py`, `studyai`, `tests`, `scripts`): passed.
- pip check: no broken requirements.
- pip-audit against `requirements.txt`: no known vulnerabilities. Cache deserialization warnings were emitted
  and ignored; the audit completed successfully.
- Gemini live smoke: passed; response contained 40 characters and remote deletion returned 200.

## Real Gemini

- Standalone minimal media smoke: PASSED.
- Three-segment pipeline through Flask → Redis → RQ worker → FFmpeg → Gemini → assembly → summary → questions:
  PASSED, including forced worker interruption and recovery.

## 3+ Hour Test

NOT EXECUTED — no authorized sample

## Final Phase 3 acceptance gates

All mandatory gates were exercised successfully. The inability of this browser harness to automate the native
file chooser is explicitly scoped above and did not prevent real resumable HTTP upload or browser recovery/UI
validation.

**SAFE TO MERGE**
