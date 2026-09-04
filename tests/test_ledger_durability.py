"""The spend ledger has to survive a restart.

The lifetime provider ceiling is a number in Redis. If that Redis starts empty
after every restart, the ceiling is not a lifetime cap at all — it is a cap
per uptime window, and `docker compose down` refills it. That is exactly what
this stack did until it was fixed, and nothing about the application code
looked wrong at the time, which is why it went unnoticed.

Asserted against the compose file because that is where the property lives.
Deleting the volume or the appendonly flag is a one-line change that reads as
tidying up.
"""

import pathlib
import re

COMPOSE = (pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()


def _redis_service() -> str:
    """The redis service block, up to the next top-level service."""
    match = re.search(r"^  redis:\n(.*?)(?=^  \w+:)", COMPOSE, re.M | re.S)
    assert match, "no redis service found in docker-compose.yml"
    return match.group(1)


def test_redis_writes_its_data_to_disk():
    service = _redis_service()

    assert "--appendonly" in service and '"yes"' in service, (
        "redis is not started with --appendonly yes, so the spend ledger only"
        " exists in memory and a restart resets the lifetime ceiling"
    )


def test_redis_has_somewhere_to_write_it():
    """appendonly without a volume is worse than useless: it writes to the
    container's own filesystem, which is discarded with the container. It
    would look configured and behave exactly as before."""
    service = _redis_service()

    assert re.search(r"volumes:\s*\n\s*-\s*redis-data:/data", service), (
        "redis has no named volume mounted at /data — the append-only file"
        " would be written to the container layer and lost on removal"
    )
    assert re.search(r"^volumes:\n(?:.*\n)*?  redis-data:", COMPOSE, re.M), (
        "redis-data is mounted but never declared under top-level volumes"
    )
