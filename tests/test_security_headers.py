from fastapi.testclient import TestClient

from app.main import app


def test_security_headers_present_on_response():
    with TestClient(app) as client:
        r = client.get("/healthz")

    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
