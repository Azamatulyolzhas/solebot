"""Integration test for ai.ask_ai quota / subscription paths.

After P1 item 3, an exhausted quota or expired subscription must:
  (a) Return CUSTOMER_UNAVAILABLE_TEXT to the caller (not the old quota_exceeded_message).
  (b) Dispatch notify_owner_* in the background.

We monkeypatch all DB / cache / notify helpers so the test stays offline.
"""
import asyncio

import ai
import billing
import notifications


def _run(coro):
    return asyncio.run(coro)


def _patch_session(monkeypatch):
    """Neutralise Redis-backed session and rate-limit helpers."""
    async def noop(*a, **kw): return None
    async def noop_false(*a, **kw): return False
    async def noop_history(*a, **kw): return []
    async def allowed_rate(*a, **kw): return True, 100

    monkeypatch.setattr(ai, "clear_chat_context", noop)
    monkeypatch.setattr(ai, "ensure_session_fresh", noop)
    monkeypatch.setattr(ai, "get_handoff_state", noop_false)
    monkeypatch.setattr(ai, "check_rate_limit", allowed_rate)
    monkeypatch.setattr(ai, "load_session_history", noop_history)
    monkeypatch.setattr(ai, "save_session_message", noop)


class TestAskAiQuotaExhausted:

    def test_returns_neutral_text_and_alerts_owner(self, monkeypatch):
        _patch_session(monkeypatch)
        monkeypatch.setattr(ai, "resolve_shop_id", lambda sid: sid)
        monkeypatch.setattr(ai, "is_subscription_active", lambda sid: True)
        monkeypatch.setattr(ai, "check_message_quota", lambda sid: (False, 500, 500))

        called = {}

        async def fake_notify(shop_id, used, limit):
            called["shop_id"] = shop_id
            called["used"] = used
            called["limit"] = limit
            return True

        monkeypatch.setattr(notifications, "notify_owner_quota_exhausted", fake_notify)

        reply = _run(ai.ask_ai("tg_1_55", "хочу кроссовки", shop_id=1))

        assert reply == billing.CUSTOMER_UNAVAILABLE_TEXT
        assert called == {"shop_id": 1, "used": 500, "limit": 500}


class TestAskAiSubscriptionExpired:

    def test_returns_neutral_text_and_alerts_owner(self, monkeypatch):
        _patch_session(monkeypatch)
        monkeypatch.setattr(ai, "resolve_shop_id", lambda sid: sid)
        monkeypatch.setattr(ai, "is_subscription_active", lambda sid: False)

        called = {}

        async def fake_notify(shop_id):
            called["shop_id"] = shop_id
            return True

        monkeypatch.setattr(notifications, "notify_owner_subscription_expired", fake_notify)

        reply = _run(ai.ask_ai("tg_1_55", "хочу кроссовки", shop_id=1))

        assert reply == billing.CUSTOMER_UNAVAILABLE_TEXT
        assert called == {"shop_id": 1}
