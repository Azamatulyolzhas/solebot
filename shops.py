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

def create_pending_shop(name: str, email: str, password_hash: str) -> int | None:
    """Create a new shop with status='pending' (awaiting admin approval)."""
    import re, time
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + f"-{int(time.time()) % 100000}"
    ph = db_placeholder()
    try:
        if USE_POSTGRES:
            row = execute_write(
                f"""
                INSERT INTO shops (name, slug, owner_email, owner_password_hash, status)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
                RETURNING id
                """,
                (name, slug, email, password_hash, "pending"),
                fetch_one=True,
            )
            return row["id"] if row else None
        else:
            execute_write(
                f"INSERT INTO shops (name, slug, owner_email, owner_password_hash, status) VALUES ({ph},{ph},{ph},{ph},{ph})",
                (name, slug, email, password_hash, "pending"),
            )
            return fetch_one_value(f"SELECT id FROM shops WHERE slug = {ph}", (slug,))
    except Exception as e:
        log.error(f"Create pending shop failed: {e}")
        return None


def update_shop_status(shop_id: int, status: str) -> bool:
    """Approve or reject a shop (set status to 'active' or 'rejected')."""
    ph = db_placeholder()
    try:
        execute_write(f"UPDATE shops SET status = {ph} WHERE id = {ph}", (status, shop_id))
        if status == "active":
            existing_sub = fetch_one_value(
                f"SELECT id FROM subscriptions WHERE shop_id = {ph} LIMIT 1", (shop_id,)
            )
            if not existing_sub:
                if USE_POSTGRES:
                    execute_write(
                        f"""
                        INSERT INTO subscriptions (shop_id, plan, status, messages_limit, channels_limit, trial_ends_at)
                        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, NOW() + INTERVAL '30 days')
                        """,
                        (shop_id, "trial", "active", 500, 1),
                    )
                else:
                    execute_write(
                        f"""
                        INSERT INTO subscriptions (shop_id, plan, status, messages_limit, channels_limit, trial_ends_at)
                        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, datetime('now', '+30 days'))
                        """,
                        (shop_id, "trial", "active", 500, 1),
                    )
        return True
    except Exception as e:
        log.error(f"Update shop status failed: {e}")
        return False


def list_pending_shops() -> list[dict]:
    try:
        return fetch_all("SELECT id, name, slug, owner_email, status, created_at FROM shops WHERE status = 'pending' ORDER BY id DESC")
    except Exception as e:
        log.error(f"List pending shops failed: {e}")
        return []


def get_shop_by_id(shop_id: int) -> dict | None:
    try:
        ph = db_placeholder()
        rows = fetch_all(
            f"SELECT id, name, slug, owner_email, status, tg_token, tg_webhook_secret, groq_system_prompt, moysklad_token, sync_api_key, created_at FROM shops WHERE id = {ph} LIMIT 1",
            (shop_id,),
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get shop by id failed: {e}")
        return None


def get_shop_by_email(email: str) -> dict | None:
    """Return shop row including password hash for authentication."""
    try:
        ph = db_placeholder()
        rows = fetch_all(
            f"SELECT id, name, slug, owner_email, owner_password_hash, status, groq_system_prompt FROM shops WHERE LOWER(owner_email) = LOWER({ph}) LIMIT 1",
            (email,),
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get shop by email failed: {e}")
        return None


def update_shop_settings(shop_id: int, name: str | None = None, groq_system_prompt: str | None = None) -> bool:
    sets = []
    params: list = []
    ph = db_placeholder()
    if name is not None:
        sets.append(f"name = {ph}")
        params.append(name)
    if groq_system_prompt is not None:
        sets.append(f"groq_system_prompt = {ph}")
        params.append(groq_system_prompt)
    if not sets:
        return False
    params.append(shop_id)
    execute_write(f"UPDATE shops SET {', '.join(sets)} WHERE id = {ph}", params)
    return True


def set_shop_owner_password(shop_id: int, password_hash: str) -> None:
    ph = db_placeholder()
    execute_write(f"UPDATE shops SET owner_password_hash = {ph} WHERE id = {ph}", (password_hash, shop_id))


def save_shop_tg_token(shop_id: int, tg_token: str, webhook_secret: str) -> None:
    """Save Telegram bot token and webhook secret for a shop."""
    ph = db_placeholder()
    execute_write(
        f"UPDATE shops SET tg_token = {ph}, tg_webhook_secret = {ph} WHERE id = {ph}",
        (tg_token, webhook_secret, shop_id),
    )


def clear_shop_tg_token(shop_id: int) -> None:
    """Remove Telegram bot token from a shop (disconnect)."""
    ph = db_placeholder()
    execute_write(
        f"UPDATE shops SET tg_token = NULL, tg_webhook_secret = NULL WHERE id = {ph}",
        (shop_id,),
    )


def get_shop_subscription_detail(shop_id: int) -> dict | None:
    try:
        ph = db_placeholder()
        rows = fetch_all(
            f"""
            SELECT plan, status, messages_limit, channels_limit, trial_ends_at, period_ends_at, created_at
            FROM subscriptions
            WHERE shop_id = {ph}
            ORDER BY id DESC LIMIT 1
            """,
            (shop_id,),
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get subscription detail failed: {e}")
        return None


def extend_shop_subscription(shop_id: int, plan: str, days: int, messages_limit: int) -> bool:
    """Manually activate or extend subscription for a shop."""
    ph = db_placeholder()
    try:
        existing = fetch_one_value(
            f"SELECT id FROM subscriptions WHERE shop_id = {ph} LIMIT 1", (shop_id,)
        )
        if existing:
            if USE_POSTGRES:
                execute_write(
                    f"""
                    UPDATE subscriptions
                    SET plan = {ph}, status = {ph}, messages_limit = {ph},
                        period_ends_at = NOW() + INTERVAL '{days} days'
                    WHERE shop_id = {ph}
                    """,
                    (plan, "active", messages_limit, shop_id),
                )
            else:
                execute_write(
                    f"""
                    UPDATE subscriptions
                    SET plan = {ph}, status = {ph}, messages_limit = {ph},
                        period_ends_at = datetime('now', '+{days} days')
                    WHERE shop_id = {ph}
                    """,
                    (plan, "active", messages_limit, shop_id),
                )
        else:
            if USE_POSTGRES:
                execute_write(
                    f"""
                    INSERT INTO subscriptions (shop_id, plan, status, messages_limit, channels_limit, period_ends_at)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, NOW() + INTERVAL '{days} days')
                    """,
                    (shop_id, plan, "active", messages_limit, 3),
                )
            else:
                execute_write(
                    f"""
                    INSERT INTO subscriptions (shop_id, plan, status, messages_limit, channels_limit, period_ends_at)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, datetime('now', '+{days} days'))
                    """,
                    (shop_id, plan, "active", messages_limit, 3),
                )
        return True
    except Exception as e:
        log.error(f"Extend subscription failed: {e}")
        return False


def is_subscription_active(shop_id: int) -> bool:
    """Check if shop has an active subscription (trial or paid, not expired)."""
    try:
        ph = db_placeholder()
        if USE_POSTGRES:
            rows = fetch_all(
                f"""
                SELECT id FROM subscriptions
                WHERE shop_id = {ph} AND status = 'active'
                  AND (
                    (trial_ends_at IS NOT NULL AND trial_ends_at > NOW()) OR
                    (period_ends_at IS NOT NULL AND period_ends_at > NOW())
                  )
                LIMIT 1
                """,
                (shop_id,),
            )
        else:
            rows = fetch_all(
                f"""
                SELECT id FROM subscriptions
                WHERE shop_id = {ph} AND status = 'active'
                  AND (
                    (trial_ends_at IS NOT NULL AND trial_ends_at > datetime('now')) OR
                    (period_ends_at IS NOT NULL AND period_ends_at > datetime('now'))
                  )
                LIMIT 1
                """,
                (shop_id,),
            )
        return bool(rows)
    except Exception as e:
        log.error(f"Check subscription failed: {e}")
        return True  # fail open — don't block bot on DB error


def get_shop_by_sync_api_key(api_key: str) -> dict | None:
    """Find shop by its sync API key."""
    try:
        ph = db_placeholder()
        rows = fetch_all(
            f"SELECT id, name, slug, moysklad_token FROM shops WHERE sync_api_key = {ph} LIMIT 1",
            (api_key,),
        )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get shop by api key failed: {e}")
        return None


def generate_sync_api_key(shop_id: int) -> str:
    """Generate and save a new sync API key for the shop."""
    import secrets
    api_key = "sk_" + secrets.token_urlsafe(32)
    ph = db_placeholder()
    execute_write(f"UPDATE shops SET sync_api_key = {ph} WHERE id = {ph}", (api_key, shop_id))
    return api_key


def save_moysklad_token(shop_id: int, token: str) -> None:
    ph = db_placeholder()
    execute_write(f"UPDATE shops SET moysklad_token = {ph} WHERE id = {ph}", (token, shop_id))


def clear_moysklad_token(shop_id: int) -> None:
    ph = db_placeholder()
    execute_write(f"UPDATE shops SET moysklad_token = NULL WHERE id = {ph}", (shop_id,))


def create_password_reset_token(shop_id: int) -> str | None:
    """Create a one-time password reset token valid for 1 hour."""
    import secrets
    token = secrets.token_urlsafe(32)
    ph = db_placeholder()
    try:
        if USE_POSTGRES:
            execute_write(
                f"""
                INSERT INTO password_reset_tokens (shop_id, token, expires_at)
                VALUES ({ph}, {ph}, NOW() + INTERVAL '1 hour')
                """,
                (shop_id, token),
            )
        else:
            execute_write(
                f"""
                INSERT INTO password_reset_tokens (shop_id, token, expires_at)
                VALUES ({ph}, {ph}, datetime('now', '+1 hour'))
                """,
                (shop_id, token),
            )
        return token
    except Exception as e:
        log.error(f"Create password reset token failed: {e}")
        return None


def get_valid_reset_token(token: str) -> dict | None:
    """Return token row if valid (not used, not expired)."""
    try:
        ph = db_placeholder()
        if USE_POSTGRES:
            rows = fetch_all(
                f"""
                SELECT id, shop_id FROM password_reset_tokens
                WHERE token = {ph} AND used = FALSE AND expires_at > NOW()
                LIMIT 1
                """,
                (token,),
            )
        else:
            rows = fetch_all(
                f"""
                SELECT id, shop_id FROM password_reset_tokens
                WHERE token = {ph} AND used = 0 AND expires_at > datetime('now')
                LIMIT 1
                """,
                (token,),
            )
        return rows[0] if rows else None
    except Exception as e:
        log.error(f"Get valid reset token failed: {e}")
        return None


def consume_reset_token(token_id: int) -> None:
    """Mark token as used so it can't be reused."""
    ph = db_placeholder()
    execute_write(f"UPDATE password_reset_tokens SET used = TRUE WHERE id = {ph}", (token_id,))


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
