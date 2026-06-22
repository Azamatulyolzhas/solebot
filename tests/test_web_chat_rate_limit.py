"""Per-IP rate limit for POST /api/chat.

The user_id passed to ask_ai is "web_{session_id}", and session_id comes from
the client body — rotating it bypasses the Redis-keyed throttle inside ask_ai.
The endpoint-level _check_web_chat_rate caps requests by remote IP so that
bypass no longer works. These tests pin the behavior.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai
import routes.api as api_route
from routes.api import router as api_router


def _make_client():
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_rate_state():
    api_route._web_chat_attempts.clear()
    yield
    api_route._web_chat_attempts.clear()


@pytest.fixture(autouse=True)
def stub_ask_ai(monkeypatch):
    async def fake(user_id, message, **kw):
        return "ok"
    monkeypatch.setattr(ai, "ask_ai", fake)
    monkeypatch.setattr(api_route, "ask_ai", fake)


class TestPerIpRateLimit:

    def test_rotating_session_id_no_longer_bypasses(self):
        client = _make_client()
        # 30 requests from the same IP with DIFFERENT session_id each time should
        # still hit the per-IP ceiling.
        for i in range(api_route._WEB_CHAT_MAX):
            resp = client.post("/api/chat", json={
                "message": f"msg {i}",
                "session_id": f"fresh-uuid-{i}",
            })
            assert resp.status_code != 429, f"Hit 429 too early at request #{i + 1}"
        # The (max+1)th request, also with a fresh session_id, must be blocked.
        resp = client.post("/api/chat", json={
            "message": "overflow",
            "session_id": "fresh-uuid-overflow",
        })
        assert resp.status_code == 429

    def test_below_limit_passes_through(self):
        client = _make_client()
        for _ in range(5):
            resp = client.post("/api/chat", json={
                "message": "hi",
                "session_id": "stable-session",
            })
            assert resp.status_code == 200
            assert resp.json()["reply"] == "ok"

    def test_429_message_is_user_friendly_russian(self):
        client = _make_client()
        for i in range(api_route._WEB_CHAT_MAX + 1):
            resp = client.post("/api/chat", json={
                "message": f"msg {i}",
                "session_id": f"s-{i}",
            })
        assert resp.status_code == 429
        assert "Подождите" in resp.json()["detail"]
