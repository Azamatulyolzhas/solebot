"""Integration tests for POST /shop/register.

We build a lightweight FastAPI app that mounts only the shop router, skipping
the full main.py lifespan (DB migrations, bot setup, etc.). All DB-layer
functions touched by shop_register are monkeypatched.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.shop as shop_route
from routes.shop import router as shop_router


def _make_client():
    """Return a TestClient for a minimal app that only includes the shop router."""
    app = FastAPI()
    app.include_router(shop_router)
    return TestClient(app, raise_server_exceptions=False)


# Reset the module-level rate-limit state before each test so tests are isolated.
@pytest.fixture(autouse=True)
def reset_rate_limit():
    shop_route._register_attempts.clear()
    yield
    shop_route._register_attempts.clear()


_VALID_BODY = {
    "shop_name": "Test Shop",
    "email": "owner@example.com",
    "password": "password123",
    "accepted_terms": True,
}


class TestShopRegisterSuccess:

    def test_register_returns_token_and_subscription_on_success(self, monkeypatch):
        monkeypatch.setattr(shop_route, "get_shop_by_email", lambda email: None)
        monkeypatch.setattr(shop_route, "create_active_shop_with_trial",
                            lambda name, email, pwd_hash, **kw: 99)
        fake_sub = {"plan": "trial", "status": "active", "messages_limit": 500}
        monkeypatch.setattr(shop_route, "get_shop_subscription_detail",
                            lambda shop_id: fake_sub)
        monkeypatch.setattr(shop_route, "send_shop_welcome",
                            lambda shop_name, owner_email, subscription=None: None)
        # hash_password calls bcrypt — allow it through (it works without DB).

        client = _make_client()
        response = client.post("/shop/register", json=_VALID_BODY)

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "token" in data
        assert data["shop_id"] == 99
        assert data["subscription"] == fake_sub


class TestShopRegisterValidation:

    def test_register_rejects_short_password(self, monkeypatch):
        monkeypatch.setattr(shop_route, "get_shop_by_email", lambda email: None)

        client = _make_client()
        body = {**_VALID_BODY, "password": "abc"}
        response = client.post("/shop/register", json=body)

        assert response.status_code == 400

    def test_register_rejects_terms_not_accepted(self, monkeypatch):
        monkeypatch.setattr(shop_route, "get_shop_by_email", lambda email: None)

        client = _make_client()
        body = {**_VALID_BODY, "accepted_terms": False}
        response = client.post("/shop/register", json=body)

        assert response.status_code == 400

    def test_register_returns_409_on_duplicate_email(self, monkeypatch):
        monkeypatch.setattr(
            shop_route, "get_shop_by_email",
            lambda email: {"id": 7, "name": "Existing Shop"}
        )

        client = _make_client()
        response = client.post("/shop/register", json=_VALID_BODY)

        assert response.status_code == 409


class TestShopRegisterRateLimit:

    def test_register_rate_limited_after_5_attempts(self, monkeypatch):
        # Make every attempt look like a new user (no existing shop) but
        # provisioning fails fast so we don't need bcrypt throughput.
        monkeypatch.setattr(shop_route, "get_shop_by_email", lambda email: None)
        monkeypatch.setattr(shop_route, "create_active_shop_with_trial", lambda *a, **kw: None)
        monkeypatch.setattr(shop_route, "send_shop_welcome", lambda *a, **kw: None)
        # Bypass bcrypt so the first 5 calls run fast.
        monkeypatch.setattr(shop_route, "hash_password", lambda pwd: "fakehash")

        client = _make_client()

        # First 5 requests should go through (may 500 due to fake_create returning None).
        for i in range(5):
            resp = client.post(
                "/shop/register",
                json={**_VALID_BODY, "email": f"user{i}@example.com"},
            )
            assert resp.status_code != 429, f"Got 429 too early on request #{i + 1}"

        # 6th request from the same IP should be rate-limited.
        resp = client.post(
            "/shop/register",
            json={**_VALID_BODY, "email": "user99@example.com"},
        )
        assert resp.status_code == 429
