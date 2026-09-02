import asyncio
import time

from fastapi.testclient import TestClient
from redis.asyncio import Redis

import app.main as main_module
from app.main import app


def test_readyz_runs_checks_concurrently_not_sequentially(isolated_db, monkeypatch):
    # Each fake check sleeps 0.3s. If they ran sequentially, /readyz would
    # take ~0.6s; run concurrently (via asyncio.gather), it should take
    # close to 0.3s — the max of the two, not the sum.
    async def slow_redis_check():
        await asyncio.sleep(0.3)
        return "ok"

    async def slow_database_check():
        await asyncio.sleep(0.3)
        return "ok"

    monkeypatch.setattr(main_module, "_check_redis", slow_redis_check)
    monkeypatch.setattr(main_module, "_check_database", slow_database_check)

    with TestClient(app) as client:
        start = time.perf_counter()
        r = client.get("/readyz")
        elapsed = time.perf_counter() - start

    assert r.status_code == 200
    assert elapsed < 0.5  # well under 0.6s (what sequential would take)


def test_readyz_reports_unavailable_when_redis_unreachable(isolated_db, monkeypatch):
    # Point at a port nothing is listening on, regardless of whether the host
    # running these tests happens to have a real Redis instance up somewhere.
    unreachable_redis = Redis.from_url("redis://localhost:1")
    monkeypatch.setattr(main_module, "get_redis", lambda: unreachable_redis)

    with TestClient(app) as client:
        r = client.get("/readyz")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "ok"
    # Just "error" — /readyz is unauthenticated, so the exception text (which
    # named hosts, ports and driver versions) is logged rather than returned.
    # What a load balancer needs is which dependency is down, not why.
    assert body["checks"]["redis"] == "error"


def test_readyz_reports_ok_when_both_dependencies_answer(isolated_db, monkeypatch):
    """The healthy path of the real checks. The concurrency test above
    replaces both of them with stubs, and the failure test only exercises the
    unhappy half — so the code that decides "this instance can serve traffic"
    was never actually run."""

    class _LiveRedis:
        async def ping(self):
            return True

        async def aclose(self):
            # The lifespan shutdown closes the client it was handed.
            pass

    monkeypatch.setattr(main_module, "get_redis", lambda: _LiveRedis())

    with TestClient(app) as client:
        r = client.get("/readyz")

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"redis": "ok", "database": "ok"}}


def test_readyz_reports_unavailable_when_the_database_is_unreachable(
    isolated_db, monkeypatch
):
    """The database half of the same contract, which had no test at all.

    A load balancer routing around an instance that cannot reach Redis but
    happily keeping one that cannot reach its audit database is the failure
    this endpoint exists to prevent.
    """

    class _LiveRedis:
        async def ping(self):
            return True

        async def aclose(self):
            # The lifespan shutdown closes the client it was handed.
            pass

    def _dead_session():
        raise OSError("could not connect to database")

    monkeypatch.setattr(main_module, "get_redis", lambda: _LiveRedis())
    monkeypatch.setattr(main_module, "async_session", _dead_session)

    with TestClient(app) as client:
        r = client.get("/readyz")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "error"
    assert body["checks"]["redis"] == "ok"
    # Same reasoning as the Redis case: the driver's message names hosts and
    # credentials, and this endpoint is unauthenticated.
    assert "could not connect" not in r.text
