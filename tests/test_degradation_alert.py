"""Degradation-spike alert (Groq storming).

Two layers:
  1. degraded_reply_stats — the indexed aggregate over analytics_events runs on the
     real (SQLite) test DB and counts degraded_* chat_reply events in the window.
  2. maybe_alert_degradation_spike — the orchestration: below ratio / below sample
     floor → no alert; above → exactly one alert; cooldown suppresses the repeat AND
     keeps the DB query to once per cooldown.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import conversations
import notifications
from db import db_placeholder, execute_write


def _run(coro):
    return asyncio.run(coro)


# ── degraded_reply_stats (real SQLite) ────────────────────────────────────────────

class TestDegradedReplyStats:
    _SHOP = 990123  # high id, isolated from other tests' data

    def _clean(self):
        # The SQLite test DB persists between runs, so start from a known state for
        # our two shop ids — makes the counts deterministic and idempotent.
        import schema
        schema.ensure_app_tables()
        ph = db_placeholder()
        execute_write(
            f"DELETE FROM analytics_events WHERE shop_id IN ({ph}, {ph})",
            (self._SHOP, self._SHOP + 1),
        )

    def _seed(self):
        self._clean()
        # 3 degraded + 2 normal recent chat_reply events for our shop.
        for mode in ("degraded_catalog", "degraded_busy", "degraded_catalog",
                     "brain_product", "ai_product"):
            conversations.log_analytics_event(
                "tg", "chat_reply", {"mode": mode}, self._SHOP
            )
        # Noise that must NOT be counted: a non-chat_reply event, another shop's
        # degraded reply, and an OLD degraded chat_reply outside any sane window.
        conversations.log_analytics_event("tg", "rate_limited", {"mode": "x"}, self._SHOP)
        conversations.log_analytics_event(
            "tg", "chat_reply", {"mode": "degraded_busy"}, self._SHOP + 1
        )
        ph = db_placeholder()
        execute_write(
            f"INSERT INTO analytics_events (shop_id, channel, event_name, payload, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
            (self._SHOP, "tg", "chat_reply",
             json.dumps({"mode": "degraded_busy"}), "2000-01-01 00:00:00"),
        )

    def test_counts_recent_degraded_only(self):
        self._seed()
        # Window keeps the 5 recent rows (3 degraded), excludes the old row, the
        # other shop, and the non-chat_reply event.
        degraded, total = conversations.degraded_reply_stats(self._SHOP, 600)
        assert degraded == 3
        assert total == 5

    def test_no_events_returns_zeroes(self):
        self._clean()
        assert conversations.degraded_reply_stats(self._SHOP, 600) == (0, 0)


# ── maybe_alert_degradation_spike (orchestration) ─────────────────────────────────

class TestDegradationAlert:
    _SHOP = 555

    def _wire(self, monkeypatch, *, degraded, total):
        notifications._OWNER_ALERT_DEDUPE.clear()
        calls = {"stats": 0}

        def fake_stats(shop_id, window):
            calls["stats"] += 1
            return degraded, total

        send = AsyncMock(return_value=True)
        monkeypatch.setattr(conversations, "degraded_reply_stats", fake_stats)
        monkeypatch.setattr(notifications, "_send_shop_telegram", send)
        monkeypatch.setattr(
            notifications, "get_shop_by_id",
            lambda sid: {"id": sid, "name": "Тест", "tg_token": "t",
                         "owner_telegram_chat_id": "1"},
        )
        return calls, send

    def test_below_ratio_no_alert(self, monkeypatch):
        calls, send = self._wire(monkeypatch, degraded=1, total=100)  # 1% < 20%
        result = _run(notifications.maybe_alert_degradation_spike(self._SHOP, "tg"))
        assert result is False
        send.assert_not_called()

    def test_below_min_samples_no_alert(self, monkeypatch):
        # Ratio is 100% but only 5 samples (< MIN_SAMPLES=10) → no alert.
        calls, send = self._wire(monkeypatch, degraded=5, total=5)
        result = _run(notifications.maybe_alert_degradation_spike(self._SHOP, "tg"))
        assert result is False
        send.assert_not_called()

    def test_above_threshold_fires_one_alert(self, monkeypatch):
        calls, send = self._wire(monkeypatch, degraded=30, total=100)  # 30% ≥ 20%
        result = _run(notifications.maybe_alert_degradation_spike(self._SHOP, "tg"))
        assert result is True
        send.assert_awaited_once()

    def test_cooldown_suppresses_repeat_and_skips_db(self, monkeypatch):
        calls, send = self._wire(monkeypatch, degraded=30, total=100)
        first = _run(notifications.maybe_alert_degradation_spike(self._SHOP, "tg"))
        second = _run(notifications.maybe_alert_degradation_spike(self._SHOP, "tg"))
        assert first is True
        assert second is False
        # Exactly one alert sent, and the DB stats query ran only once — the
        # read-only cooldown gate short-circuited the second call before the query.
        assert send.await_count == 1
        assert calls["stats"] == 1
