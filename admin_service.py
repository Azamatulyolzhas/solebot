import logging

from config import ADMIN_EMAIL, USE_POSTGRES
from auth import decode_admin_token
from db import db_placeholder, fetch_all, fetch_one_value
from fastapi import HTTPException, Request
from shops import resolve_shop_id

log = logging.getLogger(__name__)


def is_admin_configured() -> bool:
    return bool(ADMIN_EMAIL)


def _admin_authorized(request: Request) -> bool:
    query_token = request.query_params.get("token") or ""
    if query_token and decode_admin_token(query_token):
        return True

    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        if decode_admin_token(auth_header[7:].strip()):
            return True
    return False


def require_admin(request: Request) -> None:
    if not is_admin_configured():
        raise HTTPException(404, "Not found")
    if not _admin_authorized(request):
        raise HTTPException(403, "Forbidden")


def get_database_status() -> dict:
    try:
        count = fetch_one_value("SELECT COUNT(*) FROM products")
        return {
            "database": "postgresql" if USE_POSTGRES else "sqlite",
            "database_ok": True,
            "products_in_db": count,
        }
    except Exception as e:
        log.exception("Database healthcheck failed")
        return {
            "database": "postgresql" if USE_POSTGRES else "sqlite",
            "database_ok": False,
            "database_error": type(e).__name__,
        }

def count_rows(table: str) -> int:
    allowed = {"products", "conversations", "messages", "analytics_events", "orders", "shops", "subscriptions"}
    if table not in allowed:
        raise ValueError("Unsupported table")
    return fetch_one_value(f"SELECT COUNT(*) FROM {table}") or 0


def count_logged_tokens(shop_id: int | None = None) -> int:
    try:
        shop_id = resolve_shop_id(shop_id)
        if USE_POSTGRES:
            return fetch_one_value(
                f"""
                SELECT COALESCE(SUM(COALESCE((payload->>'total_tokens')::INTEGER, 0)), 0)
                FROM analytics_events
                WHERE event_name = 'chat_reply' AND shop_id = {db_placeholder()}
                """,
                (shop_id,),
            ) or 0

        return fetch_one_value(
            f"""
            SELECT COALESCE(SUM(COALESCE(json_extract(payload, '$.total_tokens'), 0)), 0)
            FROM analytics_events
            WHERE event_name = 'chat_reply' AND shop_id = {db_placeholder()}
            """,
            (shop_id,),
        ) or 0
    except Exception:
        log.exception("Token count failed")
        return 0


def list_recent_messages(limit: int = 20, shop_id: int | None = None) -> list[dict]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    return fetch_all(
        f"""
        SELECT
            m.id,
            m.role,
            m.content,
            m.created_at,
            c.channel,
            c.external_user_id
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.shop_id = {ph}
        ORDER BY m.id DESC
        LIMIT {ph}
        """,
        (shop_id, limit),
    )


def html_escape(value) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def count_shop_rows(table: str, shop_id: int | None = None) -> int:
    allowed = {"products", "conversations", "analytics_events", "orders"}
    if table not in allowed:
        raise ValueError("Unsupported shop table")
    ph = db_placeholder()
    return fetch_one_value(f"SELECT COUNT(*) FROM {table} WHERE shop_id = {ph}", (resolve_shop_id(shop_id),)) or 0

def count_shop_messages(shop_id: int | None = None) -> int:
    ph = db_placeholder()
    return fetch_one_value(
        f"""
        SELECT COUNT(*)
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.shop_id = {ph}
        """,
        (resolve_shop_id(shop_id),),
    ) or 0
