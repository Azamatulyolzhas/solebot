"""Sentry wiring with PII scrubbing.

init_sentry() is a no-op when SENTRY_DSN is empty — dev workflow stays
silent, prod gets exceptions + breadcrumbs without leaking auth tokens
or passwords.
"""
import logging

from config import ENVIRONMENT, SENTRY_DSN, SENTRY_TRACES_SAMPLE_RATE

log = logging.getLogger(__name__)

_SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key",
                      "x-telegram-bot-api-secret-token"}
_SENSITIVE_BODY_KEYS = {"password", "current_password", "new_password",
                        "token", "tg_token", "groq_api_key",
                        "moysklad_token", "api_key", "access_token"}


def _scrub_dict(d: dict) -> dict:
    """Recursively replace sensitive values with [filtered]."""
    out = {}
    for k, v in d.items():
        if isinstance(k, str) and k.lower() in _SENSITIVE_BODY_KEYS:
            out[k] = "[filtered]"
        elif isinstance(v, dict):
            out[k] = _scrub_dict(v)
        elif isinstance(v, list):
            out[k] = [_scrub_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


def _before_send(event, hint):
    """Scrub PII before Sentry transmits the event."""
    try:
        req = event.get("request") or {}
        headers = req.get("headers") or {}
        for h in list(headers):
            if h.lower() in _SENSITIVE_HEADERS:
                headers[h] = "[filtered]"

        # Scrub bodies if present (FastAPI integration may include them).
        data = req.get("data")
        if isinstance(data, dict):
            req["data"] = _scrub_dict(data)

        # Scrub query string of any ?token=... that pre-P2 admin links used.
        qs = req.get("query_string")
        if isinstance(qs, str) and "token=" in qs:
            req["query_string"] = "[filtered]"
    except Exception:
        log.exception("Sentry before_send scrub failed")
    return event


def init_sentry() -> bool:
    """Initialise Sentry once at app boot. Returns True iff init happened."""
    if not SENTRY_DSN:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=ENVIRONMENT,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
            before_send=_before_send,
            integrations=[
                StarletteIntegration(),
                FastApiIntegration(),
            ],
        )
        log.info("Sentry initialised (env=%s)", ENVIRONMENT)
        return True
    except Exception:
        log.exception("Sentry init failed — continuing without it")
        return False
