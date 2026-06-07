"""Resend domain management and production From-address resolution."""
from __future__ import annotations

import logging

import httpx

from config import EMAIL_FROM, EMAIL_FROM_NAME, RESEND_API_KEY

log = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com"
SANDBOX_FROM = "onboarding@resend.dev"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RESEND_API_KEY}"}


def is_sandbox_from(address: str | None = None) -> bool:
    addr = (address or EMAIL_FROM or "").lower()
    return "resend.dev" in addr or not addr.strip()


def list_domains() -> list[dict]:
    if not RESEND_API_KEY:
        return []
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{RESEND_API}/domains", headers=_headers())
            r.raise_for_status()
            return r.json().get("data") or []
    except Exception as e:
        log.error("Resend list domains failed: %s", e)
        return []


def get_verified_domain_name() -> str | None:
    for domain in list_domains():
        if (domain.get("status") or "").lower() == "verified":
            return domain.get("name")
    return None


def resolve_from_address() -> str:
    """Use verified domain in production; fall back to EMAIL_FROM / sandbox."""
    explicit = (EMAIL_FROM or "").strip()
    if explicit and not is_sandbox_from(explicit):
        if "<" in explicit:
            return explicit
        return f"{EMAIL_FROM_NAME} <{explicit}>"

    verified = get_verified_domain_name()
    if verified:
        return f"{EMAIL_FROM_NAME} <noreply@{verified}>"

    return explicit or SANDBOX_FROM


def get_email_status() -> dict:
    domains = list_domains()
    verified = [d for d in domains if (d.get("status") or "").lower() == "verified"]
    from_addr = resolve_from_address()
    production = not is_sandbox_from(from_addr)
    return {
        "configured": bool(RESEND_API_KEY),
        "production_ready": production,
        "sandbox_mode": not production,
        "from_address": from_addr,
        "env_from": EMAIL_FROM or None,
        "verified_domains": [d.get("name") for d in verified],
        "domains": [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "status": d.get("status"),
                "region": d.get("region"),
            }
            for d in domains
        ],
        "hint": (
            "Письма уходят на любые email клиентов."
            if production
            else "Sandbox: письма только на email аккаунта Resend. Добавьте домен ниже."
        ),
    }


def create_domain(name: str) -> dict:
    name = (name or "").strip().lower()
    if not name or " " in name:
        raise ValueError("Укажите домен, например mail.example.kz")
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY не настроен")
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{RESEND_API}/domains",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"name": name},
        )
        data = r.json()
        if r.status_code >= 400:
            msg = data.get("message") or data.get("error") or r.text
            raise ValueError(msg)
        return data


def verify_domain(domain_id: str) -> dict:
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY не настроен")
    with httpx.Client(timeout=30) as client:
        r = client.post(f"{RESEND_API}/domains/{domain_id}/verify", headers=_headers())
        data = r.json()
        if r.status_code >= 400:
            msg = data.get("message") or data.get("error") or r.text
            raise ValueError(msg)
        return data


def get_domain_records(domain_id: str) -> list[dict]:
    if not RESEND_API_KEY:
        return []
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{RESEND_API}/domains/{domain_id}", headers=_headers())
            r.raise_for_status()
            return r.json().get("records") or []
    except Exception as e:
        log.error("Resend get domain failed: %s", e)
        return []


def send_email(to: str, subject: str, html: str) -> tuple[bool, str | None]:
    """Send via Resend HTTP API. Returns (ok, error_message)."""
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"
    from_addr = resolve_from_address()
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{RESEND_API}/emails",
                headers={**_headers(), "Content-Type": "application/json"},
                json={
                    "from": from_addr,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            data = r.json()
            if r.status_code >= 400:
                err = data.get("message") or data.get("error") or r.text
                log.error("Resend send failed to=%s: %s", to, err)
                return False, str(err)
            log.info("Email sent to=%s from=%s id=%s", to, from_addr, data.get("id"))
            return True, None
    except Exception as e:
        log.error("Resend send failed to=%s: %s", to, e)
        return False, str(e)
