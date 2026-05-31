import logging

from config import DEFAULT_SHOP_NAME, DEFAULT_SHOP_SLUG, USE_POSTGRES
from db import db_placeholder, execute_write, fetch_all, fetch_one_value

log = logging.getLogger(__name__)


def get_default_shop_id() -> int:
    ph = db_placeholder()
    if USE_POSTGRES:
        row = execute_write(
            f"""
            INSERT INTO shops (name, slug)
            VALUES ({ph}, {ph})
            ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (DEFAULT_SHOP_NAME, DEFAULT_SHOP_SLUG),
            fetch_one=True,
        )
        return row["id"]

    execute_write(
        f"INSERT OR IGNORE INTO shops (name, slug) VALUES ({ph}, {ph})",
        (DEFAULT_SHOP_NAME, DEFAULT_SHOP_SLUG),
    )
    return fetch_one_value(f"SELECT id FROM shops WHERE slug = {ph}", (DEFAULT_SHOP_SLUG,))

def ensure_default_shop_data() -> None:
    try:
        shop_id = get_default_shop_id()
        ph = db_placeholder()
        execute_write(f"UPDATE sneakers SET shop_id = {ph} WHERE shop_id IS NULL", (shop_id,))
        execute_write(f"UPDATE orders SET shop_id = {ph} WHERE shop_id IS NULL", (shop_id,))
        execute_write(f"UPDATE conversations SET shop_id = {ph} WHERE shop_id IS NULL", (shop_id,))

        existing_sub = fetch_one_value(
            f"SELECT id FROM subscriptions WHERE shop_id = {ph} AND status = {ph} LIMIT 1",
            (shop_id, "active"),
        )
        if existing_sub:
            return

        if USE_POSTGRES:
            execute_write(
                f"""
                INSERT INTO subscriptions
                    (shop_id, plan, status, messages_limit, channels_limit, trial_ends_at)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, NOW() + INTERVAL '30 days')
                """,
                (shop_id, "trial", "active", 500, 3),
            )
        else:
            execute_write(
                f"""
                INSERT INTO subscriptions
                    (shop_id, plan, status, messages_limit, channels_limit, trial_ends_at)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, datetime('now', '+30 days'))
                """,
                (shop_id, "trial", "active", 500, 3),
            )
    except Exception as e:
        log.error(f"Default shop data setup failed: {e}")


def resolve_shop_id(shop_id: int | None = None) -> int:
    return shop_id if shop_id is not None else get_default_shop_id()

def list_shops() -> list[dict]:
    try:
        return fetch_all("""
            SELECT s.id, s.name, s.slug, s.owner_email, s.status, s.created_at,
                   s.tg_webhook_secret,
                   CASE WHEN s.tg_token IS NULL OR s.tg_token = '' THEN false ELSE true END AS has_tg_token,
                   sub.plan,
                   sub.status AS sub_status,
                   sub.messages_limit,
                   sub.channels_limit,
                   sub.trial_ends_at,
                   sub.period_ends_at
            FROM shops s
            LEFT JOIN subscriptions sub ON sub.shop_id = s.id
            ORDER BY s.id DESC
        """)
    except Exception as e:
        log.error(f"List shops failed: {e}")
        return []

def get_all_active_telegram_shops() -> list[dict]:
    try:
        return fetch_all("""
            SELECT id, name, slug, tg_token, tg_webhook_secret
            FROM shops
            WHERE tg_token IS NOT NULL
              AND tg_token <> ''
              AND tg_webhook_secret IS NOT NULL
              AND tg_webhook_secret <> ''
              AND status = 'active'
            ORDER BY id
        """)
    except Exception as e:
        log.error(f"Get active Telegram shops failed: {e}")
        return []

def get_shop_by_webhook_secret(secret: str) -> dict | None:
    try:
        ph = db_placeholder()
        rows = fetch_all(
            f"""
            SELECT id, name, slug, tg_token, tg_webhook_secret
            FROM shops
            WHERE tg_webhook_secret = {ph} AND status = {ph}
            LIMIT 1
            """,
            (secret, "active"),
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get shop by webhook secret failed: {e}")
        return None
