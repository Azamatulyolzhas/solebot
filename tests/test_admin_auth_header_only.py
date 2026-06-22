"""Verify admin auth accepts ONLY Authorization: Bearer headers.

Previously _admin_authorized also accepted ?token=... query strings, which leak
into reverse-proxy access logs, browser history, and Referer headers. P2 item 2
removed that path. These tests pin the behavior so it cannot regress.
"""
from unittest.mock import patch

import admin_service


class _FakeRequest:
    def __init__(self, query: dict | None = None, headers: dict | None = None):
        self.query_params = query or {}
        self.headers = headers or {}


class TestAdminAuthHeaderOnly:

    def test_valid_bearer_header_authorizes(self):
        with patch.object(admin_service, "decode_admin_token", return_value={"sub": "admin"}):
            req = _FakeRequest(headers={"Authorization": "Bearer good-token"})
            assert admin_service._admin_authorized(req) is True

    def test_query_string_token_no_longer_authorizes(self):
        # The query path is gone — even a token that WOULD decode is ignored.
        with patch.object(admin_service, "decode_admin_token", return_value={"sub": "admin"}) as decode:
            req = _FakeRequest(query={"token": "would-be-valid"})
            assert admin_service._admin_authorized(req) is False
            decode.assert_not_called()

    def test_bearer_with_invalid_token_denied(self):
        with patch.object(admin_service, "decode_admin_token", return_value=None):
            req = _FakeRequest(headers={"Authorization": "Bearer bad-token"})
            assert admin_service._admin_authorized(req) is False

    def test_no_credentials_denied(self):
        req = _FakeRequest()
        assert admin_service._admin_authorized(req) is False

    def test_non_bearer_authorization_scheme_denied(self):
        with patch.object(admin_service, "decode_admin_token", return_value={"sub": "admin"}) as decode:
            req = _FakeRequest(headers={"Authorization": "Basic dXNlcjpwYXNz"})
            assert admin_service._admin_authorized(req) is False
            decode.assert_not_called()
