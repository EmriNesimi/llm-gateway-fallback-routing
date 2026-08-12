#!/usr/bin/env sh
# Run schema migrations once, before the app starts — not on every request
# or raced by concurrent replicas. Only meaningful for a real (non-SQLite)
# DATABASE_URL; against the default SQLite file, app/db/session.py's
# init_db() (create_all) already handles it at app startup.
set -e

case "$DATABASE_URL" in
  sqlite*|"") ;;
  *)
    # docker-compose.yml's `depends_on: postgres: condition: service_healthy`
    # covers the common case, but that dependency ordering doesn't exist
    # under every orchestrator (Kubernetes, notably) — so this retries
    # rather than assuming the DB is already reachable the instant this
    # container starts. A connection failure here fails cleanly before any
    # migration has applied, so retrying the whole command is safe.
    echo "Running database migrations..."
    attempt=1
    max_attempts=10
    until alembic upgrade head; do
      if [ "$attempt" -ge "$max_attempts" ]; then
        echo "Migrations failed after $max_attempts attempts, giving up." >&2
        exit 1
      fi
      echo "Migration attempt $attempt/$max_attempts failed, retrying in 3s..." >&2
      attempt=$((attempt + 1))
      sleep 3
    done
    ;;
esac

exec "$@"
