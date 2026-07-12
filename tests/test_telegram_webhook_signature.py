"""Item 4 — verify /tg/{secret}/webhook drops requests that lack the
X-Telegram-Bot-Api-Secret-Token header (or carry a wrong value).

Telegram echoes the per-bot secret_token (passed at setWebhook time) in
that header on every webhook delivery. If we don't check it, the secret
in the URL is the only barrier — and anyone who scrapes it from logs or
a misconfigured proxy can spoof updates.
"""
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.api import router as api_router


def _make_client():
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app, raise_server_exceptions=False)


SECRET = "very-secret-webhook-token-32-chars-abc"


class TestShopWebhookSignature:

    def test_missing_header_returns_403(self):
        client = _make_client()
        resp = client.post(f"/tg/{SECRET}/webhook", json={})
        assert resp.status_code == 403
        assert "signature" in resp.json()["detail"].lower()

    def test_wrong_header_returns_403(self):
        client = _make_client()
        resp = client.post(
            f"/tg/{SECRET}/webhook",
            json={},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_correct_header_passes_signature_check(self, monkeypatch):
        # process_shop_update is async — patch with AsyncMock so the body
        # parses and we never touch real DB or Telegram libs.
        fake_process = AsyncMock(return_value={"id": 5})
        with patch("telegram_bot.process_shop_update", fake_process):
            client = _make_client()
            resp = client.post(
                f"/tg/{SECRET}/webhook",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "shop_id": 5}
        fake_process.assert_called_once()


class TestDefaultWebhookSignature:

    def test_default_webhook_no_secret_configured_skips_check(self, monkeypatch):
        # If TELEGRAM_WEBHOOK_SECRET is empty (dev / not configured), the
        # signature check is skipped so we don't accidentally lock ourselves
        # out of an existing default-bot setup.
        import config as _cfg

        monkeypatch.setattr(_cfg, "TELEGRAM_WEBHOOK_SECRET", "")

        # Stub tg_bot truthy + process_default_update so we get to a 200.
        import telegram_bot as _tg
        monkeypatch.setattr(_tg, "tg_bot", object())
        monkeypatch.setattr(_tg, "process_default_update",
                            AsyncMock(return_value=None))

        client = _make_client()
        resp = client.post("/tg/webhook", json={"update_id": 1})
        assert resp.status_code == 200

    def test_default_webhook_with_secret_rejects_missing_header(self, monkeypatch):
        import config as _cfg
        import telegram_bot as _tg

        monkeypatch.setattr(_cfg, "TELEGRAM_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(_tg, "tg_bot", object())

        client = _make_client()
        resp = client.post("/tg/webhook", json={"update_id": 1})
        assert resp.status_code == 403
