"""Subscription checks, message quotas, and per-shop Groq API keys."""
import logging
from datetime import datetime

from config import GROQ_API_KEY, USE_POSTGRES
from db import db_placeholder, fetch_all, fetch_one_value

log = logging.getLogger(__name__)

UNLIMITED_MESSAGES = 999_999


def get_active_subscription(shop_id: int) -> dict | None:
    """Return subscription row only if status active and not expired."""
    try:
        ph = db_placeholder()
        if USE_POSTGRES:
            rows = fetch_all(
                f"""
                SELECT *
                FROM subscriptions
                WHERE shop_id = {ph} AND status = 'active'
                  AND (
                    (trial_ends_at IS NOT NULL AND trial_ends_at > NOW()) OR
                    (period_ends_at IS NOT NULL AND period_ends_at > NOW())
                  )
                ORDER BY id DESC
                LIMIT 1
                """,
                (shop_id,),
            )
        else:
            rows = fetch_all(
                f"""
                SELECT *
                FROM subscriptions
                WHERE shop_id = {ph} AND status = 'active'
                  AND (
                    (trial_ends_at IS NOT NULL AND trial_ends_at > datetime('now')) OR
                    (period_ends_at IS NOT NULL AND period_ends_at > datetime('now'))
                  )
                ORDER BY id DESC
                LIMIT 1
                """,
                (shop_id,),
            )
        return rows[0] if rows else None
    except Exception:
        log.exception("Get active subscription failed for shop %s", shop_id)
        return None


def is_subscription_active(shop_id: int) -> bool:
    return get_active_subscription(shop_id) is not None


def _period_start(sub: dict) -> datetime | str | None:
    return sub.get("period_starts_at") or sub.get("created_at")


def count_messages_used(shop_id: int, sub: dict | None = None) -> int:
    """Count bot replies (chat_reply) in the current billing period."""
    sub = sub or get_active_subscription(shop_id)
    if not sub:
        return 0
    period_start = _period_start(sub)
    if not period_start:
        return 0
    try:
        ph = db_placeholder()
        if USE_POSTGRES:
            return fetch_one_value(
                f"""
                SELECT COUNT(*)
                FROM analytics_events
                WHERE shop_id = {ph}
                  AND event_name = 'chat_reply'
                  AND created_at >= {ph}
                """,
                (shop_id, period_start),
            ) or 0
        return fetch_one_value(
            f"""
            SELECT COUNT(*)
            FROM analytics_events
            WHERE shop_id = {ph}
              AND event_name = 'chat_reply'
              AND created_at >= {ph}
            """,
            (shop_id, period_start),
        ) or 0
    except Exception:
        log.exception("Count messages used failed for shop %s", shop_id)
        return 0


def check_message_quota(shop_id: int) -> tuple[bool, int, int]:
    """Return (allowed, used, limit). Unlimited plans always allowed."""
    sub = get_active_subscription(shop_id)
    if not sub:
        return False, 0, 0
    limit = int(sub.get("messages_limit") or 0)
    if limit <= 0 or limit >= UNLIMITED_MESSAGES:
        return True, count_messages_used(shop_id, sub), limit
    used = count_messages_used(shop_id, sub)
    return used < limit, used, limit


def quota_exceeded_message(used: int, limit: int) -> str:
    return (
        f"Лимит сообщений магазина исчерпан ({used} из {limit}). "
        "Обратитесь к владельцу магазина для продления подписки."
    )


def resolve_groq_api_key(shop_id: int) -> str | None:
    """Per-shop BYOK key, else platform default."""
    try:
        from shops import get_shop_by_id

        shop = get_shop_by_id(shop_id)
        own = ((shop or {}).get("groq_api_key") or "").strip()
        if own:
            return own
    except Exception:
        log.exception("Resolve Groq key failed for shop %s", shop_id)
    platform = (GROQ_API_KEY or "").strip()
    return platform or None


def subscription_usage(shop_id: int) -> dict:
    sub = get_active_subscription(shop_id)
    if not sub:
        return {"active": False, "messages_used": 0, "messages_limit": 0, "messages_remaining": 0}
    used = count_messages_used(shop_id, sub)
    limit = int(sub.get("messages_limit") or 0)
    if limit <= 0 or limit >= UNLIMITED_MESSAGES:
        remaining = None
    else:
        remaining = max(limit - used, 0)
    return {
        "active": True,
        "messages_used": used,
        "messages_limit": limit,
        "messages_remaining": remaining,
        "unlimited": limit >= UNLIMITED_MESSAGES,
    }
