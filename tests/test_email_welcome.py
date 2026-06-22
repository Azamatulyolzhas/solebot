"""Unit tests for email_service.send_shop_welcome.

_send and RESEND_API_KEY are monkeypatched so no real HTTP calls are made.
"""
import email_service


class TestSendShopWelcome:

    def test_send_shop_welcome_escapes_shop_name(self, monkeypatch):
        captured = {}

        def fake_send(to, subject, html):
            captured["to"] = to
            captured["subject"] = subject
            captured["html"] = html
            return True

        monkeypatch.setattr(email_service, "_send", fake_send)

        email_service.send_shop_welcome(
            shop_name="<script>alert(1)</script>",
            owner_email="owner@example.com",
        )

        assert "<script>" not in captured["html"]
        assert "&lt;script&gt;" in captured["html"]
        assert "<script>" not in captured["subject"]
        assert "&lt;script&gt;" in captured["subject"]

    def test_send_shop_welcome_no_resend_key_returns_false(self, monkeypatch):
        # Patch the module-level attribute that _send reads.
        monkeypatch.setattr(email_service, "RESEND_API_KEY", "")

        result = email_service.send_shop_welcome(
            shop_name="Good Shop",
            owner_email="owner@example.com",
        )

        assert result is False

    def test_subscription_rows_included_when_subscription_passed(self, monkeypatch):
        captured = {}

        def fake_send(to, subject, html):
            captured["html"] = html
            return True

        monkeypatch.setattr(email_service, "_send", fake_send)

        subscription = {
            "plan": "trial",
            "status": "active",
            "messages_limit": 500,
            "trial_ends_at": "2026-07-06",
            "period_ends_at": None,
        }
        email_service.send_shop_welcome(
            shop_name="My Store",
            owner_email="owner@example.com",
            subscription=subscription,
        )

        assert "Ваш пробный доступ" in captured["html"]
