"""Health endpoint returns 200/503 for UptimeRobot.
/api/support exposes the configured contact info.
"""
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.api import router as api_router


def _make_client():
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app, raise_server_exceptions=False)


class TestHealthStatusCode:

    def test_returns_200_when_db_ok(self):
        with patch("routes.api.get_database_status",
                   return_value={"database": "sqlite", "database_ok": True, "products_in_db": 0}), \
             patch("routes.api.get_redis_status", new=AsyncMock(return_value={"redis": "ok"})), \
             patch("email_service.email_delivery_status",
                   return_value={"configured": False, "production_ready": False, "from_address": "x@y"}):
            client = _make_client()
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database_ok"] is True

    def test_returns_503_when_db_down(self):
        with patch("routes.api.get_database_status",
                   return_value={"database": "postgresql", "database_ok": False,
                                 "database_error": "OperationalError"}), \
             patch("routes.api.get_redis_status", new=AsyncMock(return_value={"redis": "ok"})), \
             patch("email_service.email_delivery_status",
                   return_value={"configured": False, "production_ready": False, "from_address": "x@y"}):
            client = _make_client()
            resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database_ok"] is False
        # Detailed body preserved for human inspection
        assert body.get("database_error") == "OperationalError"


class TestSupportEndpoint:

    def test_returns_both_when_set(self, monkeypatch):
        import config as _cfg
        monkeypatch.setattr(_cfg, "SUPPORT_EMAIL", "help@vendly.kz")
        monkeypatch.setattr(_cfg, "SUPPORT_TELEGRAM", "vendly_support")
        client = _make_client()
        resp = client.get("/api/support")
        assert resp.status_code == 200
        assert resp.json() == {"email": "help@vendly.kz", "telegram": "vendly_support"}

    def test_returns_nulls_when_unset(self, monkeypatch):
        import config as _cfg
        monkeypatch.setattr(_cfg, "SUPPORT_EMAIL", "")
        monkeypatch.setattr(_cfg, "SUPPORT_TELEGRAM", "")
        client = _make_client()
        resp = client.get("/api/support")
        assert resp.status_code == 200
        assert resp.json() == {"email": None, "telegram": None}
