"""Sentry wiring contract tests.

We don't want a real Sentry DSN in CI, so init_sentry must no-op when the
env var is empty. The PII-scrub function is unit-tested directly so the
behaviour is pinned regardless of whether the real Sentry SDK is loaded
in tests.
"""
import observability


class TestInitSentry:

    def test_noop_when_dsn_empty(self, monkeypatch):
        monkeypatch.setattr(observability, "SENTRY_DSN", "")
        assert observability.init_sentry() is False


class TestScrubBeforeSend:

    def test_filters_auth_header(self):
        event = {"request": {"headers": {
            "Authorization": "Bearer leak-me",
            "Content-Type": "application/json",
        }}}
        out = observability._before_send(event, hint={})
        assert out["request"]["headers"]["Authorization"] == "[filtered]"
        assert out["request"]["headers"]["Content-Type"] == "application/json"

    def test_filters_cookie_and_api_key_headers(self):
        event = {"request": {"headers": {
            "Cookie": "session=abc",
            "X-API-Key": "sk_live_xxx",
            "X-Telegram-Bot-Api-Secret-Token": "tg-secret",
        }}}
        out = observability._before_send(event, hint={})
        h = out["request"]["headers"]
        assert h["Cookie"] == "[filtered]"
        assert h["X-API-Key"] == "[filtered]"
        assert h["X-Telegram-Bot-Api-Secret-Token"] == "[filtered]"

    def test_filters_password_in_body(self):
        event = {"request": {"data": {
            "email": "x@y.com",
            "password": "should-not-leak",
            "shop_name": "Test",
        }}}
        out = observability._before_send(event, hint={})
        d = out["request"]["data"]
        assert d["password"] == "[filtered]"
        assert d["email"] == "x@y.com"
        assert d["shop_name"] == "Test"

    def test_filters_nested_tokens(self):
        event = {"request": {"data": {
            "settings": {
                "groq_api_key": "gsk_xxx",
                "name": "Shop",
            },
        }}}
        out = observability._before_send(event, hint={})
        nested = out["request"]["data"]["settings"]
        assert nested["groq_api_key"] == "[filtered]"
        assert nested["name"] == "Shop"

    def test_filters_query_string_token(self):
        event = {"request": {"query_string": "page=1&token=secret-abc"}}
        out = observability._before_send(event, hint={})
        assert out["request"]["query_string"] == "[filtered]"

    def test_passes_through_when_nothing_sensitive(self):
        event = {"request": {"headers": {"Content-Type": "application/json"},
                             "query_string": "page=2"}}
        out = observability._before_send(event, hint={})
        assert out["request"]["headers"]["Content-Type"] == "application/json"
        assert out["request"]["query_string"] == "page=2"
