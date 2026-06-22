"""Tests for owner-facing quota and subscription alerts.

The alerts run on the same code path that previously sent quota messages to the
customer. We monkeypatch the Telegram send so tests stay offline.
"""
import asyncio

import notifications


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _patch_send(monkeypatch, sent: list):
    async def fake_send(shop, text):
        sent.append({"shop_id": shop.get("id"), "text": text})
        return True
    monkeypatch.setattr(notifications, "_send_shop_telegram", fake_send)


def _patch_shop(monkeypatch, shop: dict | None = None):
    monkeypatch.setattr(
        notifications, "get_shop_by_id",
        lambda sid: shop if shop is None or shop.get("id") == sid else None,
    )


def _patch_resolve(monkeypatch):
    monkeypatch.setattr(notifications, "resolve_shop_id", lambda sid: sid)


class TestQuotaAlert:

    def test_sends_once_then_deduped(self, monkeypatch):
        notifications._OWNER_ALERT_DEDUPE.clear()
        sent: list = []
        _patch_send(monkeypatch, sent)
        _patch_shop(monkeypatch, {"id": 5, "name": "Test", "tg_token": "x", "owner_telegram_chat_id": "123"})
        _patch_resolve(monkeypatch)

        ok1 = _run(notifications.notify_owner_quota_exhausted(5, 500, 500))
        ok2 = _run(notifications.notify_owner_quota_exhausted(5, 501, 500))

        assert ok1 is True
        assert ok2 is False, "second call within 24h should be deduped"
        assert len(sent) == 1
        assert "Лимит сообщений" in sent[0]["text"]
        assert "500 из 500" in sent[0]["text"]

    def test_returns_false_when_shop_missing(self, monkeypatch):
        notifications._OWNER_ALERT_DEDUPE.clear()
        sent: list = []
        _patch_send(monkeypatch, sent)
        monkeypatch.setattr(notifications, "get_shop_by_id", lambda sid: None)
        _patch_resolve(monkeypatch)

        ok = _run(notifications.notify_owner_quota_exhausted(99, 100, 100))
        assert ok is False
        assert sent == []


class TestSubscriptionAlert:

    def test_sends_once_then_deduped(self, monkeypatch):
        notifications._OWNER_ALERT_DEDUPE.clear()
        sent: list = []
        _patch_send(monkeypatch, sent)
        _patch_shop(monkeypatch, {"id": 7, "name": "Sub Shop", "tg_token": "y", "owner_telegram_chat_id": "456"})
        _patch_resolve(monkeypatch)

        ok1 = _run(notifications.notify_owner_subscription_expired(7))
        ok2 = _run(notifications.notify_owner_subscription_expired(7))

        assert ok1 is True
        assert ok2 is False
        assert len(sent) == 1
        assert "Подписка" in sent[0]["text"]


class TestDedupeIsolation:

    def test_quota_and_subscription_dont_share_dedupe(self, monkeypatch):
        notifications._OWNER_ALERT_DEDUPE.clear()
        sent: list = []
        _patch_send(monkeypatch, sent)
        _patch_shop(monkeypatch, {"id": 3, "name": "S", "tg_token": "z", "owner_telegram_chat_id": "789"})
        _patch_resolve(monkeypatch)

        ok_quota = _run(notifications.notify_owner_quota_exhausted(3, 100, 100))
        ok_sub = _run(notifications.notify_owner_subscription_expired(3))

        assert ok_quota is True
        assert ok_sub is True
        assert len(sent) == 2

    def test_different_shops_dont_share_dedupe(self, monkeypatch):
        notifications._OWNER_ALERT_DEDUPE.clear()
        sent: list = []
        _patch_send(monkeypatch, sent)
        # get_shop_by_id returns a shop with matching id
        monkeypatch.setattr(
            notifications, "get_shop_by_id",
            lambda sid: {"id": sid, "name": f"Shop {sid}", "tg_token": "t", "owner_telegram_chat_id": "1"},
        )
        _patch_resolve(monkeypatch)

        ok1 = _run(notifications.notify_owner_quota_exhausted(1, 100, 100))
        ok2 = _run(notifications.notify_owner_quota_exhausted(2, 100, 100))

        assert ok1 is True
        assert ok2 is True
        assert len(sent) == 2
