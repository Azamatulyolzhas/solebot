import logging


log = logging.getLogger(__name__)


def get_shop_subscription(shop_id: int) -> dict | None:
    """Return an active subscription for a shop.

    This module is intentionally small for now; the implementation imports DB
    helpers lazily to avoid circular imports while the monolith is being split.
    """
    try:
        from db import db_placeholder, fetch_all

        ph = db_placeholder()
        rows = fetch_all(
            f"SELECT * FROM subscriptions WHERE shop_id = {ph} AND status = {ph} LIMIT 1",
            (shop_id, "active"),
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get shop subscription failed: {e}")
        return None


def is_subscription_active(shop_id: int) -> bool:
    return get_shop_subscription(shop_id) is not None
