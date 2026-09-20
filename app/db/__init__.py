"""The database: client keys, the request audit log, and the admin audit log.

SQLite by default, Postgres by opt-in (decision 002). Schema changes are
Alembic migrations under migrations/; tests/test_migrations.py fails the
build if models.py drifts from them.
"""
