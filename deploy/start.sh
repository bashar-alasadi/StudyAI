#!/bin/sh
set -eu

python worker.py &
worker_pid=$!

gunicorn \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --timeout "${WEB_TIMEOUT_SECONDS:-120}" \
  --access-logfile - \
  --error-logfile - \
  app:app &
web_pid=$!

shutdown() {
  kill -TERM "$web_pid" "$worker_pid" 2>/dev/null || true
  wait "$web_pid" "$worker_pid" 2>/dev/null || true
}

trap shutdown TERM INT

while kill -0 "$web_pid" 2>/dev/null && kill -0 "$worker_pid" 2>/dev/null; do
  sleep 2
done

shutdown
exit 1
