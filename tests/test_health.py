from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_points_at_the_docs():
    """The only unauthenticated, undocumented endpoint, and the first URL
    anyone opens after starting the stack. It had no test at all, so a rename
    of /docs would leave the landing page pointing somewhere that 404s."""
    r = client.get("/")

    assert r.status_code == 200
    assert r.json() == {"service": "llm-gateway", "docs": "/docs"}

    # The link it hands out has to actually resolve.
    assert client.get(r.json()["docs"]).status_code == 200
