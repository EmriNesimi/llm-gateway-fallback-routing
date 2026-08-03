import pytest
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.main import app


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", "test-admin-secret")


def test_create_list_and_revoke_key(isolated_db):
    with TestClient(app) as client:
        r = client.post(
            "/admin/keys", json={"team": "acme"}, headers={"X-Admin-Key": "test-admin-secret"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["team"] == "acme"
        assert len(body["api_key"]) > 10

        r = client.get("/admin/keys", headers={"X-Admin-Key": "test-admin-secret"})
        keys = r.json()
        assert len(keys) == 1
        assert keys[0]["team"] == "acme"
        assert keys[0]["revoked"] is False
        key_id = keys[0]["id"]

        r = client.delete(f"/admin/keys/{key_id}", headers={"X-Admin-Key": "test-admin-secret"})
        assert r.status_code == 200
        assert r.json()["revoked"] is True

        r = client.get("/admin/keys", headers={"X-Admin-Key": "test-admin-secret"})
        assert r.json()[0]["revoked"] is True


def test_admin_endpoints_require_admin_key(isolated_db):
    with TestClient(app) as client:
        r = client.get("/admin/keys")
        assert r.status_code == 401

        r = client.get("/admin/keys", headers={"X-Admin-Key": "wrong"})
        assert r.status_code == 401


def test_admin_api_fails_closed_when_unconfigured(isolated_db, monkeypatch):
    monkeypatch.setattr(admin_auth.settings, "admin_api_key", None)
    with TestClient(app) as client:
        r = client.get("/admin/keys", headers={"X-Admin-Key": "anything"})
        assert r.status_code == 503


def test_revoke_unknown_key_returns_404(isolated_db):
    with TestClient(app) as client:
        r = client.delete("/admin/keys/999", headers={"X-Admin-Key": "test-admin-secret"})
        assert r.status_code == 404
