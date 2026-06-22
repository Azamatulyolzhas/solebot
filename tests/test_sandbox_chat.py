"""Integration tests for POST /shop/sandbox-chat.

Same minimal-FastAPI pattern as test_register_endpoint.py.
get_current_shop is overridden via FastAPI dependency_overrides so we don't have
to deal with JWT issuance; ai.sandbox_reply is patched directly because the
route imports it inside the handler body.
"""
import ai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.shop as shop_route
from routes.shop import router as shop_router, get_current_shop


_FAKE_SHOP = {"id": 42, "name": "Test Shop", "status": "active", "email_verified": True}


def _make_client():
    app = FastAPI()
    app.include_router(shop_router)
    app.dependency_overrides[get_current_shop] = lambda: _FAKE_SHOP
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_sandbox_rate():
    shop_route._sandbox_attempts.clear()
    yield
    shop_route._sandbox_attempts.clear()


def _fake_reply(reply="Привет!", mode="ai_product", products=None):
    async def _stub(shop_id, user_message, history=None):
        return {"reply": reply, "mode": mode, "products": products or []}
    return _stub


class TestSandboxChatSuccess:

    def test_returns_reply_mode_products(self, monkeypatch):
        monkeypatch.setattr(
            ai, "sandbox_reply",
            _fake_reply(
                reply="Есть синие 42 размера за 18 000 ₸.",
                mode="ai_product",
                products=[{"name": "Кроссовки Nike", "price": 18000}],
            ),
        )
        client = _make_client()
        resp = client.post("/shop/sandbox-chat", json={"message": "синие 42"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"].startswith("Есть синие")
        assert data["mode"] == "ai_product"
        assert data["products"][0]["name"] == "Кроссовки Nike"

    def test_passes_history_to_sandbox_reply(self, monkeypatch):
        seen = {}

        async def capture(shop_id, user_message, history=None):
            seen["shop_id"] = shop_id
            seen["message"] = user_message
            seen["history"] = history
            return {"reply": "ok", "mode": "ai_product", "products": []}

        monkeypatch.setattr(ai, "sandbox_reply", capture)
        client = _make_client()
        body = {
            "message": "ещё что-нибудь?",
            "history": [
                {"role": "user", "content": "хочу кроссовки"},
                {"role": "assistant", "content": "есть синие"},
            ],
        }
        resp = client.post("/shop/sandbox-chat", json=body)

        assert resp.status_code == 200
        assert seen["shop_id"] == 42
        assert seen["message"] == "ещё что-нибудь?"
        assert len(seen["history"]) == 2
        assert seen["history"][0]["content"] == "хочу кроссовки"

    def test_history_drops_invalid_roles_and_blank_content(self, monkeypatch):
        seen = {}

        async def capture(shop_id, user_message, history=None):
            seen["history"] = history
            return {"reply": "ok", "mode": "ai_product", "products": []}

        monkeypatch.setattr(ai, "sandbox_reply", capture)
        client = _make_client()
        body = {
            "message": "test",
            "history": [
                {"role": "system", "content": "ignored"},
                {"role": "user", "content": "   "},
                {"role": "user", "content": "kept"},
            ],
        }
        resp = client.post("/shop/sandbox-chat", json=body)

        assert resp.status_code == 200
        assert seen["history"] == [{"role": "user", "content": "kept"}]


class TestSandboxChatValidation:

    def test_empty_message_rejected(self, monkeypatch):
        monkeypatch.setattr(ai, "sandbox_reply", _fake_reply())
        client = _make_client()
        resp = client.post("/shop/sandbox-chat", json={"message": "   "})
        assert resp.status_code == 400

    def test_too_long_message_rejected(self, monkeypatch):
        monkeypatch.setattr(ai, "sandbox_reply", _fake_reply())
        client = _make_client()
        resp = client.post("/shop/sandbox-chat", json={"message": "x" * 2001})
        assert resp.status_code == 400


class TestSandboxChatRateLimit:

    def test_rate_limited_after_30_messages(self, monkeypatch):
        monkeypatch.setattr(ai, "sandbox_reply", _fake_reply())
        client = _make_client()

        for i in range(shop_route._SANDBOX_MAX):
            resp = client.post("/shop/sandbox-chat", json={"message": f"msg {i}"})
            assert resp.status_code != 429, f"Hit 429 too early at request #{i + 1}"

        resp = client.post("/shop/sandbox-chat", json={"message": "overflow"})
        assert resp.status_code == 429
