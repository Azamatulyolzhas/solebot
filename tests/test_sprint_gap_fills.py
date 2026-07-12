"""Targeted gap-fill tests for the cumulative sprint diff.

Three gaps filled:
  1. _claim_owner_alert 24-h boundary — the dedupe window is strictly < 24h,
     so a call at exactly 24h after the first must be allowed through.
  2. ensure_app_tables idempotency — calling it twice on the same DB must not
     raise (the analytics_events index uses CREATE INDEX IF NOT EXISTS).
  3. CUSTOMER_UNAVAILABLE_TEXT info-leak guard — the constant must not embed
     quota numbers, shop names, or template placeholders.
"""
import os
import sqlite3
import tempfile

import notifications
import billing
import email_service


# ---------------------------------------------------------------------------
# 1. Dedupe time-window boundary
# ---------------------------------------------------------------------------

class TestClaimOwnerAlertBoundary:

    def test_allows_alert_at_exactly_24h_boundary(self, monkeypatch):
        """A call at t=BASE and a follow-up at t=BASE+24h should both return True.

        We use a realistic epoch-like timestamp because last==0.0 (default for an
        unseen key) means 'never alerted'. With fake_now=0 the diff would be 0,
        which the guard would (correctly) treat as 'just alerted'.
        """
        notifications._OWNER_ALERT_DEDUPE.clear()
        BASE = 1_000_000.0  # well above _OWNER_ALERT_WINDOW_SECONDS so first call passes
        fake_now = [BASE]
        monkeypatch.setattr(notifications.time, "time", lambda: fake_now[0])

        first = notifications._claim_owner_alert(42, "quota")
        assert first is True

        # Advance time by exactly 24h (== _OWNER_ALERT_WINDOW_SECONDS)
        fake_now[0] = BASE + notifications._OWNER_ALERT_WINDOW_SECONDS
        # The guard is `now - last < window`, so at equality it is NOT less-than → allowed
        second = notifications._claim_owner_alert(42, "quota")
        assert second is True, "alert at the 24h mark should be permitted (boundary is exclusive)"

    def test_blocks_alert_one_second_before_24h_boundary(self, monkeypatch):
        """A call at t=BASE then at t=BASE+24h-1s must still be deduped."""
        notifications._OWNER_ALERT_DEDUPE.clear()
        BASE = 1_000_000.0
        fake_now = [BASE]
        monkeypatch.setattr(notifications.time, "time", lambda: fake_now[0])

        notifications._claim_owner_alert(43, "subscription")

        fake_now[0] = BASE + notifications._OWNER_ALERT_WINDOW_SECONDS - 1
        result = notifications._claim_owner_alert(43, "subscription")
        assert result is False, "alert one second before 24h window must be suppressed"


# ---------------------------------------------------------------------------
# 2. ensure_app_tables idempotency
# ---------------------------------------------------------------------------

class TestEnsureAppTablesIdempotent:

    def test_calling_twice_does_not_raise(self, monkeypatch):
        """CREATE INDEX IF NOT EXISTS must make double-init safe."""
        tmpfd, tmppath = tempfile.mkstemp(suffix=".db")
        os.close(tmpfd)
        try:
            import schema
            import db as _db
            monkeypatch.setattr(schema, "USE_POSTGRES", False)
            monkeypatch.setattr(_db, "DB_PATH", tmppath)
            monkeypatch.setattr(_db, "USE_POSTGRES", False)

            schema.ensure_app_tables()
            # Second call must not raise OperationalError or any other exception
            schema.ensure_app_tables()

            conn = sqlite3.connect(tmppath)
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='analytics_events'"
                ).fetchall()
            finally:
                conn.close()
            index_names = [r[0] for r in rows]
            assert "idx_analytics_events_shop_event_time" in index_names
        finally:
            try:
                os.unlink(tmppath)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 3. CUSTOMER_UNAVAILABLE_TEXT info-leak guard
# ---------------------------------------------------------------------------

class TestCustomerUnavailableText:

    def test_constant_contains_no_quota_numbers(self):
        """Must not embed numeric quota values that could expose billing details."""
        import re
        text = billing.CUSTOMER_UNAVAILABLE_TEXT
        # No standalone integers (quota counts, limits, etc.)
        assert not re.search(r"\b\d+\b", text), (
            f"CUSTOMER_UNAVAILABLE_TEXT contains numbers: {text!r}"
        )

    def test_constant_contains_no_template_placeholders(self):
        """Must be a fully-rendered string, not a format template."""
        text = billing.CUSTOMER_UNAVAILABLE_TEXT
        assert "{" not in text and "}" not in text, (
            f"CUSTOMER_UNAVAILABLE_TEXT looks like an unrendered template: {text!r}"
        )

    def test_constant_is_non_empty_string(self):
        text = billing.CUSTOMER_UNAVAILABLE_TEXT
        assert isinstance(text, str) and len(text.strip()) > 0


# ---------------------------------------------------------------------------
# 4. send_shop_welcome with subscription=None (no trial_ends_at crash)
# ---------------------------------------------------------------------------

class TestSendShopWelcomeNoneSubscription:

    def test_should_not_crash_when_subscription_is_none(self, monkeypatch):
        """_fmt_date(None) must return '—', not raise AttributeError."""
        captured = {}

        def fake_send(to, subject, html):
            captured["html"] = html
            return True

        monkeypatch.setattr(email_service, "_send", fake_send)

        # Should not raise
        result = email_service.send_shop_welcome(
            shop_name="No Trial Shop",
            owner_email="owner@example.com",
            subscription=None,
        )

        assert result is True
        # The date placeholder for trial_ends falls back to em-dash
        assert "—" in captured["html"]
        # The optional sub_block is absent
        assert "Ваш пробный доступ" not in captured["html"]

    def test_should_not_crash_when_subscription_has_no_trial_ends_at(self, monkeypatch):
        """A subscription dict missing trial_ends_at must not raise."""
        captured = {}

        def fake_send(to, subject, html):
            captured["html"] = html
            return True

        monkeypatch.setattr(email_service, "_send", fake_send)

        result = email_service.send_shop_welcome(
            shop_name="Shop Without Date",
            owner_email="owner@example.com",
            subscription={"plan": "trial", "status": "active", "messages_limit": 500},
        )

        assert result is True
        assert "—" in captured["html"]
