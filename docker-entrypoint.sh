#!/usr/bin/env sh
# Run schema migrations once, before the app starts — not on every request
# or raced by concurrent replicas. Only meaningful for a real (non-SQLite)
# DATABASE_URL; against the default SQLite file, app/db/session.py's
# init_db() (create_all) already handles it at app startup.
set -e

case "$DATABASE_URL" in
  sqlite*|"") ;;
  *)
    echo "Running database migrations..."
    alembic upgrade head
    ;;
esac

exec "$@"
