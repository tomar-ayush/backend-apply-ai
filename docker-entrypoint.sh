#!/bin/sh
set -e

# Apply database migrations (idempotent: only unapplied revisions run).
echo "Running database migrations..."
alembic upgrade head

# Hand off to the main process (uvicorn).
echo "Starting application..."
exec "$@"
