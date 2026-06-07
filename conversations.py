import json
import logging

from config import USE_POSTGRES
from db import db_placeholder, execute_write, fetch_all, fetch_one_value
from shops import resolve_shop_id

log = logging.getLogger(__name__)


def split_user_id(user_id: str) -> tuple[str, str]:
    """Parse channel user ids: tg_{shop_id}_{telegram_id}, wa_{phone}, web_{session}."""
    if not user_id:
        return "unknown", ""
    if user_id.startswith("tg_"):
        parts = user_id.split("_")
        if len(parts) >= 3:
            return "tg", "_".join(parts[2:])
        if len(parts) == 2:
            return "tg", parts[1]
    if "_" in user_id:
        channel, external_user_id = user_id.split("_", 1)
        return channel, external_user_id
    return "unknown", user_id


def get_or_create_conversation(channel: str, external_user_id: str, shop_id: int | None = None) -> int:
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    if USE_POSTGRES:
        row = execute_write(
            f"""
            INSERT INTO conversations (shop_id, channel, external_user_id, updated_at)
            VALUES ({ph}, {ph}, {ph}, NOW())
            ON CONFLICT (shop_id, channel, external_user_id)
            DO UPDATE SET updated_at = NOW()
            RETURNING id
            """,
            (shop_id, channel, external_user_id),
            fetch_one=True,
        )
        return row["id"]

    execute_write(
        f"""
        INSERT OR IGNORE INTO conversations (shop_id, channel, external_user_id)
        VALUES ({ph}, {ph}, {ph})
        """,
        (shop_id, channel, external_user_id),
    )
    execute_write(
        f"""
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE shop_id = {ph} AND channel = {ph} AND external_user_id = {ph}
        """,
        (shop_id, channel, external_user_id),
    )
    return fetch_one_value(
        f"""
        SELECT id FROM conversations
        WHERE shop_id = {ph} AND channel = {ph} AND external_user_id = {ph}
        """,
        (shop_id, channel, external_user_id),
    )

def save_message(conversation_id: int, role: str, content: str) -> None:
    ph = db_placeholder()
    execute_write(
        f"INSERT INTO messages (conversation_id, role, content) VALUES ({ph}, {ph}, {ph})",
        (conversation_id, role, content),
    )

def load_recent_messages(conversation_id: int, limit: int = 6) -> list[dict]:
    ph = db_placeholder()
    rows = fetch_all(
        f"""
        SELECT role, content
        FROM messages
        WHERE conversation_id = {ph}
        ORDER BY id DESC
        LIMIT {ph}
        """,
        (conversation_id, limit),
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

def log_analytics_event(channel: str, event_name: str, payload: dict, shop_id: int | None = None) -> None:
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    payload_value = json.dumps(payload, ensure_ascii=False)
    payload_expr = f"{ph}::jsonb" if USE_POSTGRES else ph
    execute_write(
        f"INSERT INTO analytics_events (shop_id, channel, event_name, payload) VALUES ({ph}, {ph}, {ph}, {payload_expr})",
        (shop_id, channel, event_name, payload_value),
    )
