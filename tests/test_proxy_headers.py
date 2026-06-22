"""Audit follow-up F1 — per-IP rate limit must use X-Forwarded-For behind a proxy.

Railway and most reverse proxies present every request as coming from the
edge node's IP. Without X-Forwarded-For trust, _check_*_rate buckets collapse
into a single bucket and the limit becomes global instead of per-user.

These tests pin _real_client_ip's contract in both api and shop route modules.
"""
from unittest.mock import MagicMock

import routes.api as api_route
import routes.shop as shop_route


def _req(headers=None, client_host="10.0.0.1"):
    r = MagicMock()
    r.headers = headers or {}
    r.client = MagicMock()
    r.client.host = client_host
    return r


class TestApiRealClientIp:

    def test_prefers_x_forwarded_for_leftmost(self):
        r = _req(headers={"x-forwarded-for": "203.0.113.7, 10.0.0.5, 10.0.0.1"})
        assert api_route._real_client_ip(r) == "203.0.113.7"

    def test_falls_back_to_request_client_when_no_xff(self):
        r = _req(headers={})
        assert api_route._real_client_ip(r) == "10.0.0.1"

    def test_blank_xff_falls_back(self):
        r = _req(headers={"x-forwarded-for": "   "})
        assert api_route._real_client_ip(r) == "10.0.0.1"

    def test_unknown_when_no_client(self):
        r = MagicMock()
        r.headers = {}
        r.client = None
        assert api_route._real_client_ip(r) == "unknown"


class TestShopRealClientIp:

    def test_prefers_x_forwarded_for_leftmost(self):
        r = _req(headers={"x-forwarded-for": "198.51.100.4, 10.0.0.2"})
        assert shop_route._real_client_ip(r) == "198.51.100.4"

    def test_falls_back_to_request_client(self):
        r = _req(client_host="10.0.0.99")
        assert shop_route._real_client_ip(r) == "10.0.0.99"
