from fastapi.testclient import TestClient
from redis.asyncio import Redis

import app.main as main_module
from app.main import app


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
    assert body["checks"]["redis"].startswith("error:")
