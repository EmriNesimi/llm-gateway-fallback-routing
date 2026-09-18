"""Guards against the exact class of bug this migration setup exists to
prevent: a model changes (like adding `request_id` to AuditLogEntry did)
but nobody generates/commits the matching Alembic migration.

Runs `alembic upgrade head` against a throwaway DB, then `alembic check` —
which fails if the current models would still produce new migration
operations, meaning the committed migrations are out of sync with the code.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_migrations_match_current_models():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "migration_check.db"
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"

        # check=False on purpose, here and below: the return code is asserted
        # by hand so the failure message carries alembic's own output. With
        # check=True the CalledProcessError would say only "exit status 1".
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, (
            "models have changed without a matching migration:\n"
            + check.stdout
            + check.stderr
        )
