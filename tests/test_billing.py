"""Unit tests for billing.py quota logic.

DB access (get_active_subscription / count_messages_used) is monkeypatched so
the boundary arithmetic in check_message_quota is tested in isolation.
"""
import billing


class TestCheckMessageQuota:
    def test_no_subscription_blocks(self, monkeypatch):
        monkeypatch.setattr(billing, "get_active_subscription", lambda sid: None)
        allowed, used, limit = billing.check_message_quota(1)
        assert allowed is False
        assert (used, limit) == (0, 0)

    def test_under_limit_allowed(self, monkeypatch):
        monkeypatch.setattr(
            billing, "get_active_subscription", lambda sid: {"messages_limit": 100}
        )
        monkeypatch.setattr(billing, "count_messages_used", lambda sid, sub=None: 50)
        allowed, used, limit = billing.check_message_quota(1)
        assert allowed is True
        assert (used, limit) == (50, 100)

    def test_at_limit_blocks(self, monkeypatch):
        monkeypatch.setattr(
            billing, "get_active_subscription", lambda sid: {"messages_limit": 100}
        )
        monkeypatch.setattr(billing, "count_messages_used", lambda sid, sub=None: 100)
        allowed, used, limit = billing.check_message_quota(1)
        assert allowed is False
        assert (used, limit) == (100, 100)

    def test_unlimited_plan_always_allowed(self, monkeypatch):
        monkeypatch.setattr(
            billing,
            "get_active_subscription",
            lambda sid: {"messages_limit": billing.UNLIMITED_MESSAGES},
        )
        monkeypatch.setattr(billing, "count_messages_used", lambda sid, sub=None: 10**9)
        allowed, _used, _limit = billing.check_message_quota(1)
        assert allowed is True


class TestQuotaExceededMessage:
    def test_includes_numbers(self):
        msg = billing.quota_exceeded_message(100, 100)
        assert "100" in msg


class TestSubscriptionUsage:
    def test_inactive_shape(self, monkeypatch):
        monkeypatch.setattr(billing, "get_active_subscription", lambda sid: None)
        usage = billing.subscription_usage(1)
        assert usage["active"] is False
        assert usage["messages_remaining"] == 0

    def test_active_remaining_math(self, monkeypatch):
        monkeypatch.setattr(
            billing, "get_active_subscription", lambda sid: {"messages_limit": 500}
        )
        monkeypatch.setattr(billing, "count_messages_used", lambda sid, sub=None: 120)
        usage = billing.subscription_usage(1)
        assert usage["active"] is True
        assert usage["messages_used"] == 120
        assert usage["messages_remaining"] == 380
