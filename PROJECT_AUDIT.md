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
