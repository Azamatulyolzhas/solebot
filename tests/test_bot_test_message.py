"""Tests for POST /shop/bot-test-message — owner smoke-test of Telegram wiring."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.shop as shop_route
from routes.shop import router as shop_router, get_current_shop


_FAKE_SHOP = {"id": 11, "name": "Test Shop", "status": "active", "email_verified": True}


def _make_client(shop_override=None):
    app = FastAPI()
    app.include_router(shop_router)
    app.dependency_overrides[get_current_shop] = lambda: shop_override or _FAKE_SHOP
    return TestClient(app, raise_server_exceptions=False)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Records posts; returns whatever the test set up."""
    response = _FakeResponse({"ok": True})
    raises: Exception | None = None
    last_url: str | None = None
    last_json: dict | None = None

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json=None):
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_json = json
        if _FakeAsyncClient.raises:
            raise _FakeAsyncClient.raises
        return _FakeAsyncClient.response


@pytest.fixture(autouse=True)
def reset_fake_client():
    _FakeAsyncClient.response = _FakeResponse({"ok": True})
    _FakeAsyncClient.raises = None
    _FakeAsyncClient.last_url = None
    _FakeAsyncClient.last_json = None


class TestBotTestMessage:

    def test_success(self, monkeypatch):
        full_shop = {
            **_FAKE_SHOP,
            "tg_token": "12345:ABCDE",
            "owner_telegram_chat_id": "987654",
        }
        monkeypatch.setattr(shop_route, "get_shop_by_id", lambda sid: full_shop)
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        client = _make_client()
        resp = client.post("/shop/bot-test-message")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert "12345:ABCDE" in _FakeAsyncClient.last_url
        assert _FakeAsyncClient.last_json["chat_id"] == 987654
        assert "Test Shop" in _FakeAsyncClient.last_json["text"]

    def test_missing_token_returns_400(self, monkeypatch):
        monkeypatch.setattr(
            shop_route, "get_shop_by_id",
            lambda sid: {**_FAKE_SHOP, "tg_token": "", "owner_telegram_chat_id": "1"},
        )
        client = _make_client()
        resp = client.post("/shop/bot-test-message")
        assert resp.status_code == 400
        assert "подключите" in resp.json()["detail"].lower()

    def test_missing_chat_id_returns_400(self, monkeypatch):
        monkeypatch.setattr(
            shop_route, "get_shop_by_id",
            lambda sid: {**_FAKE_SHOP, "tg_token": "tok", "owner_telegram_chat_id": ""},
        )
        client = _make_client()
        resp = client.post("/shop/bot-test-message")
        assert resp.status_code == 400
        assert "telegram id" in resp.json()["detail"].lower()

    def test_telegram_says_chat_not_found(self, monkeypatch):
        monkeypatch.setattr(
            shop_route, "get_shop_by_id",
            lambda sid: {**_FAKE_SHOP, "tg_token": "tok", "owner_telegram_chat_id": "1"},
        )
        _FakeAsyncClient.response = _FakeResponse(
            {"ok": False, "description": "Bad Request: chat not found"},
            status_code=400,
        )
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        client = _make_client()
        resp = client.post("/shop/bot-test-message")
        assert resp.status_code == 400
        assert "/start" in resp.json()["detail"]

    def test_httpx_error_returns_502(self, monkeypatch):
        monkeypatch.setattr(
            shop_route, "get_shop_by_id",
            lambda sid: {**_FAKE_SHOP, "tg_token": "tok", "owner_telegram_chat_id": "1"},
        )
        _FakeAsyncClient.raises = httpx.ConnectError("network down")
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        client = _make_client()
        resp = client.post("/shop/bot-test-message")
        assert resp.status_code == 502
