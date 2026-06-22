"""Item 1 — email verification gate.

After registration the owner gets a JWT and can browse the dashboard, but
any endpoint that connects an external account, hands out a sync key, or
burns Groq quota must reject the request until verify-email is clicked.

Pins:
  - require_verified_shop returns 403 when email_verified is false.
  - /shop/verify-email with a valid token flips the flag and consumes
    the token.
  - /shop/verify-email with an expired/used/missing token returns 400.
  - /shop/resend-verification (auth) issues a fresh token.
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.shop as shop_route
from routes.shop import (
    get_current_shop,
    require_verified_shop,
    router as shop_router,
)


def _make_client(shop_override=None):
    app = FastAPI()
    app.include_router(shop_router)
    if shop_override is not None:
        app.dependency_overrides[get_current_shop] = lambda: shop_override
    return TestClient(app, raise_server_exceptions=False)


class TestRequireVerifiedShop:

    def test_unverified_shop_blocked(self):
        unverified = {"id": 1, "name": "S", "status": "active",
                      "owner_email": "x@y.com", "email_verified": False}
        with pytest.raises(Exception) as exc:
            require_verified_shop(unverified)
        # FastAPI HTTPException carries .status_code
        assert getattr(exc.value, "status_code", None) == 403
        assert "email" in str(exc.value.detail).lower()

    def test_verified_shop_passes(self):
        verified = {"id": 1, "name": "S", "status": "active",
                    "owner_email": "x@y.com", "email_verified": True}
        result = require_verified_shop(verified)
        assert result is verified


class TestVerifyEmailEndpoint:

    def test_valid_token_flips_verified_flag(self, monkeypatch):
        marked = {}
        consumed = []

        monkeypatch.setattr(shop_route, "get_valid_email_verification_token",
                            lambda t: {"id": 99, "shop_id": 5} if t == "good" else None)
        monkeypatch.setattr(shop_route, "mark_email_verified",
                            lambda sid: marked.setdefault("shop_id", sid))
        monkeypatch.setattr(shop_route, "consume_email_verification_token",
                            lambda tid: consumed.append(tid))

        client = _make_client()
        resp = client.post("/shop/verify-email", json={"token": "good"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert marked == {"shop_id": 5}
        assert consumed == [99]

    def test_invalid_token_returns_400(self, monkeypatch):
        monkeypatch.setattr(shop_route, "get_valid_email_verification_token",
                            lambda t: None)
        client = _make_client()
        resp = client.post("/shop/verify-email", json={"token": "expired-or-used"})
        assert resp.status_code == 400


class TestResendVerification:

    def test_resend_for_unverified_emails_new_token(self, monkeypatch):
        unverified = {"id": 7, "owner_email": "u@v.com", "email_verified": False,
                      "name": "S", "status": "active"}
        sent = []

        monkeypatch.setattr(shop_route, "create_email_verification_token",
                            lambda sid: "fresh-token")
        monkeypatch.setattr(shop_route, "send_email_verification",
                            lambda email, token: sent.append((email, token)))

        client = _make_client(shop_override=unverified)
        resp = client.post("/shop/resend-verification")
        assert resp.status_code == 200
        assert sent == [("u@v.com", "fresh-token")]

    def test_resend_for_verified_is_noop(self, monkeypatch):
        verified = {"id": 7, "owner_email": "u@v.com", "email_verified": True,
                    "name": "S", "status": "active"}
        sent = []
        monkeypatch.setattr(shop_route, "send_email_verification",
                            lambda email, token: sent.append((email, token)))

        client = _make_client(shop_override=verified)
        resp = client.post("/shop/resend-verification")
        assert resp.status_code == 200
        assert resp.json().get("already_verified") is True
        assert sent == []


class TestGatedEndpoints:
    """Smoke-test that one representative gated endpoint actually rejects
    an unverified shop. The handler itself is mocked away — we only care
    about the dependency."""

    def test_sandbox_chat_rejects_unverified(self):
        unverified = {"id": 1, "owner_email": "x@y.com",
                      "email_verified": False, "status": "active", "name": "S"}
        app = FastAPI()
        app.include_router(shop_router)
        app.dependency_overrides[get_current_shop] = lambda: unverified

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/shop/sandbox-chat", json={"message": "test"})
        assert resp.status_code == 403
        assert "email" in resp.json()["detail"].lower()
