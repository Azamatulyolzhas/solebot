
import os
import sqlite3
import json
import logging
import re
import time
import csv
import io
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from dotenv import load_dotenv

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

# Telegram
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK   = os.getenv("TELEGRAM_WEBHOOK_URL", "")   # https://yourdomain.com/tg/webhook

WHATSAPP_TOKEN     = os.getenv("WHATSAPP_TOKEN", "")          # Meta Business token
WHATSAPP_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY    = os.getenv("WHATSAPP_VERIFY_TOKEN", "mysecret")

INSTAGRAM_TOKEN    = os.getenv("INSTAGRAM_TOKEN", "")
INSTAGRAM_VERIFY   = os.getenv("INSTAGRAM_VERIFY_TOKEN", "mysecret")
ADMIN_TOKEN        = os.getenv("ADMIN_TOKEN", "")
MANAGER_TELEGRAM_CHAT_ID = os.getenv("MANAGER_TELEGRAM_CHAT_ID", "")

DB_PATH = os.getenv("DB_PATH", "sneakers.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)
DEFAULT_SHOP_SLUG = os.getenv("SHOP_SLUG", "default")
DEFAULT_SHOP_NAME = os.getenv("SHOP_NAME", "Default shop")
REDIS_URL = os.getenv("REDIS_URL", "")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
RATE_LIMIT_MESSAGES = int(os.getenv("RATE_LIMIT_MESSAGES", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# ─── База данных ──────────────────────────────────────────────────────
def get_db():
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_placeholder() -> str:
    return "%s" if USE_POSTGRES else "?"

def fetch_all(query: str, params: list | tuple = ()) -> list[dict]:
    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def fetch_one_value(query: str, params: list | tuple = ()):
    conn = get_db()
    row = conn.execute(query, params).fetchone()
    conn.close()
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]

def execute_write(query: str, params: list | tuple = (), fetch_one: bool = False):
    conn = get_db()
    try:
        cur = conn.execute(query, params)
        row = cur.fetchone() if fetch_one else None
        conn.commit()
        if row is None:
            return None
        return dict(row) if not isinstance(row, tuple) else row
    finally:
        conn.close()

def ensure_app_tables() -> None:
    conn = get_db()
    try:
        if USE_POSTGRES:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shops (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id BIGSERIAL PRIMARY KEY,
                    shop_id BIGINT REFERENCES shops(id),
                    channel TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (shop_id, channel, external_user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id BIGSERIAL PRIMARY KEY,
                    shop_id BIGINT REFERENCES shops(id),
                    channel TEXT,
                    event_name TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id BIGSERIAL PRIMARY KEY,
                    shop_id BIGINT REFERENCES shops(id),
                    sneaker_id BIGINT REFERENCES sneakers(id),
                    customer_name TEXT,
                    customer_phone TEXT,
                    channel TEXT,
                    external_user_id TEXT,
                    product_interest TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            for ddl in (
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shop_id BIGINT REFERENCES shops(id)",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS channel TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_user_id TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_interest TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new'",
            ):
                conn.execute(ddl)
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER,
                    channel TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (shop_id, channel, external_user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER,
                    channel TEXT,
                    event_name TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER,
                    sneaker_id INTEGER,
                    customer_name TEXT,
                    customer_phone TEXT,
                    channel TEXT,
                    external_user_id TEXT,
                    product_interest TEXT,
                    status TEXT DEFAULT 'new',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            existing_order_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            for column, ddl in {
                "shop_id": "ALTER TABLE orders ADD COLUMN shop_id INTEGER",
                "channel": "ALTER TABLE orders ADD COLUMN channel TEXT",
                "external_user_id": "ALTER TABLE orders ADD COLUMN external_user_id TEXT",
                "product_interest": "ALTER TABLE orders ADD COLUMN product_interest TEXT",
                "status": "ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'new'",
            }.items():
                if column not in existing_order_columns:
                    conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()

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

def split_user_id(user_id: str) -> tuple[str, str]:
    if "_" not in user_id:
        return "unknown", user_id
    channel, external_user_id = user_id.split("_", 1)
    return channel, external_user_id

def get_or_create_conversation(channel: str, external_user_id: str) -> int:
    shop_id = get_default_shop_id()
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

def log_analytics_event(channel: str, event_name: str, payload: dict) -> None:
    shop_id = get_default_shop_id()
    ph = db_placeholder()
    payload_value = json.dumps(payload, ensure_ascii=False)
    payload_expr = f"{ph}::jsonb" if USE_POSTGRES else ph
    execute_write(
        f"INSERT INTO analytics_events (shop_id, channel, event_name, payload) VALUES ({ph}, {ph}, {ph}, {payload_expr})",
        (shop_id, channel, event_name, payload_value),
    )

def get_database_status() -> dict:
    try:
        count = fetch_one_value("SELECT COUNT(*) FROM sneakers")
        return {
            "database": "postgresql" if USE_POSTGRES else "sqlite",
            "database_ok": True,
            "sneakers_in_db": count,
        }
    except Exception as e:
        log.exception("Database healthcheck failed")
        return {
            "database": "postgresql" if USE_POSTGRES else "sqlite",
            "database_ok": False,
            "database_error": type(e).__name__,
            "sneakers_in_db": 0,
        }

def count_rows(table: str) -> int:
    allowed = {"sneakers", "conversations", "messages", "analytics_events", "orders"}
    if table not in allowed:
        raise ValueError("Unsupported table")
    return fetch_one_value(f"SELECT COUNT(*) FROM {table}") or 0

def create_order(
    channel: str,
    external_user_id: str,
    customer_name: str,
    customer_phone: str,
    product_interest: str,
) -> int | None:
    try:
        shop_id = get_default_shop_id()
        ph = db_placeholder()
        row = execute_write(
            f"""
            INSERT INTO orders
                (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, status)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
            """,
            (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, "new"),
            fetch_one=True,
        ) if USE_POSTGRES else None
        if USE_POSTGRES:
            return row["id"] if row else None

        execute_write(
            f"""
            INSERT INTO orders
                (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, status)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """,
            (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, "new"),
        )
        return fetch_one_value("SELECT MAX(id) FROM orders")
    except Exception as e:
        log.error(f"Create order failed: {e}")
        return None

def list_orders(limit: int = 100, offset: int = 0) -> list[dict]:
    ph = db_placeholder()
    try:
        return fetch_all(
            f"""
            SELECT id, channel, external_user_id, customer_name, customer_phone,
                   product_interest, status, created_at
            FROM orders
            ORDER BY id DESC
            LIMIT {ph} OFFSET {ph}
            """,
            (limit, offset),
        )
    except Exception as e:
        log.error(f"List orders failed: {e}")
        return []

def count_logged_tokens() -> int:
    try:
        if USE_POSTGRES:
            return fetch_one_value(
                """
                SELECT COALESCE(SUM(COALESCE((payload->>'total_tokens')::INTEGER, 0)), 0)
                FROM analytics_events
                WHERE event_name = 'chat_reply'
                """
            ) or 0

        return fetch_one_value(
            """
            SELECT COALESCE(SUM(COALESCE(json_extract(payload, '$.total_tokens'), 0)), 0)
            FROM analytics_events
            WHERE event_name = 'chat_reply'
            """
        ) or 0
    except Exception:
        log.exception("Token count failed")
        return 0

def require_admin(request: Request) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(404, "Not found")
    if request.query_params.get("token") != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")

def list_products(limit: int = 100, offset: int = 0) -> list[dict]:
    ph = db_placeholder()
    return fetch_all(
        f"""
        SELECT id, brand, model, colorway, size, quantity, price, category, gender
        FROM sneakers
        ORDER BY brand, model, colorway, size
        LIMIT {ph} OFFSET {ph}
        """,
        (limit, offset),
    )

def list_recent_messages(limit: int = 20) -> list[dict]:
    ph = db_placeholder()
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
        ORDER BY m.id DESC
        LIMIT {ph}
        """,
        (limit,),
    )

def html_escape(value) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def render_admin_page(token: str) -> str:
    stats = {
        "Products": count_rows("sneakers"),
        "Orders": count_rows("orders"),
        "Conversations": count_rows("conversations"),
        "Messages": count_rows("messages"),
        "Events": count_rows("analytics_events"),
        "Tokens": count_logged_tokens(),
    }
    products = list_products(limit=80)
    messages = list_recent_messages(limit=20)

    stat_cards = "".join(
        f"<section class='metric'><span>{label}</span><strong>{value}</strong></section>"
        for label, value in stats.items()
    )
    product_rows = "".join(
        "<tr>"
        f"<td>{html_escape(p['brand'])}</td>"
        f"<td>{html_escape(p['model'])}</td>"
        f"<td>{html_escape(p.get('colorway'))}</td>"
        f"<td>{html_escape(p['size'])}</td>"
        f"<td>{html_escape(p['quantity'])}</td>"
        f"<td>{html_escape(p['price'])} ₸</td>"
        f"<td>{html_escape(p.get('category'))}</td>"
        f"<td>{html_escape(p.get('gender'))}</td>"
        "</tr>"
        for p in products
    )
    message_items = "".join(
        "<li>"
        f"<div><strong>{html_escape(m['channel'])}</strong> "
        f"<span>{html_escape(m['external_user_id'])}</span> "
        f"<em>{html_escape(m['role'])}</em></div>"
        f"<p>{html_escape(m['content'])}</p>"
        "</li>"
        for m in messages
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SoleBot Admin</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #687385;
      --line: #dfe4ea;
      --accent: #0f766e;
      --accent-dark: #115e59;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 24px clamp(16px, 4vw, 40px);
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
    main {{ padding: 24px clamp(16px, 4vw, 40px); display: grid; gap: 24px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ padding: 16px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 28px; }}
    .panel {{ overflow: hidden; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 16px;
      border-bottom: 1px solid var(--line);
    }}
    h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    form {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    input[type=file] {{ max-width: 260px; }}
    button, .button {{
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      padding: 9px 12px;
      font-weight: 650;
      cursor: pointer;
      text-decoration: none;
      font-size: 14px;
    }}
    button:hover, .button:hover {{ background: var(--accent-dark); }}
    .table-wrap {{ overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 760px; }}
    th, td {{ padding: 11px 14px; border-bottom: 1px solid var(--line); text-align: left; font-size: 14px; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; background: #fbfcfd; }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{ padding: 14px 16px; border-bottom: 1px solid var(--line); }}
    li div {{ display: flex; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 13px; }}
    li p {{ margin: 8px 0 0; line-height: 1.45; }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panel-head {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>SoleBot Admin</h1>
    </div>
    <div class="actions">
      <a class="button" href="/admin/import-template?token={html_escape(token)}">CSV template</a>
      <a class="button" href="/admin/export?token={html_escape(token)}">Export CSV</a>
    </div>
  </header>
  <main>
    <section class="metrics">{stat_cards}</section>

    <section class="panel">
      <div class="panel-head">
        <h2>Catalog Import</h2>
        <form action="/admin/import-preview?token={html_escape(token)}" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".csv" required>
          <button type="submit">Preview CSV</button>
        </form>
        <form action="/admin/import?token={html_escape(token)}" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".csv" required>
          <button type="submit">Update CSV</button>
        </form>
        <form action="/admin/import?token={html_escape(token)}&replace=true" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".csv" required>
          <button type="submit">Replace catalog</button>
        </form>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Products</h2>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Brand</th><th>Model</th><th>Color</th><th>Size</th>
              <th>Qty</th><th>Price</th><th>Category</th><th>Gender</th>
            </tr>
          </thead>
          <tbody>{product_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Recent Messages</h2>
      </div>
      <ul>{message_items}</ul>
    </section>
  </main>
</body>
</html>"""

def parse_product_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    required = {"brand", "model", "size", "quantity", "price"}
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    missing = required - headers
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

    products = []
    for line_no, row in enumerate(reader, start=2):
        try:
            brand = (row.get("brand") or "").strip()
            model = (row.get("model") or "").strip()
            if not brand or not model:
                raise ValueError("brand/model are required")

            products.append({
                "brand": brand,
                "model": model,
                "colorway": (row.get("colorway") or "").strip() or None,
                "size": float(str(row.get("size", "")).replace(",", ".")),
                "quantity": int(row.get("quantity", 0)),
                "price": int(row.get("price", 0)),
                "category": (row.get("category") or "").strip() or None,
                "gender": (row.get("gender") or "").strip() or None,
            })
        except Exception as e:
            raise ValueError(f"Invalid row {line_no}: {e}") from e

    if not products:
        raise ValueError("CSV has no products")
    return products

def validate_product_csv(content: bytes) -> dict:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        return {"valid": False, "products": [], "errors": [f"File encoding error: {e}"]}

    reader = csv.DictReader(io.StringIO(text))
    required = {"brand", "model", "size", "quantity", "price"}
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    missing = required - headers
    errors = []
    products = []

    if missing:
        errors.append(f"Missing columns: {', '.join(sorted(missing))}")
        return {"valid": False, "products": [], "errors": errors}

    for line_no, row in enumerate(reader, start=2):
        try:
            brand = (row.get("brand") or "").strip()
            model = (row.get("model") or "").strip()
            if not brand:
                raise ValueError("brand is required")
            if not model:
                raise ValueError("model is required")

            size = float(str(row.get("size", "")).replace(",", "."))
            quantity = int(row.get("quantity", 0))
            price = int(row.get("price", 0))
            if quantity < 0:
                raise ValueError("quantity cannot be negative")
            if price <= 0:
                raise ValueError("price must be greater than 0")

            products.append({
                "brand": brand,
                "model": model,
                "colorway": (row.get("colorway") or "").strip() or None,
                "size": size,
                "quantity": quantity,
                "price": price,
                "category": (row.get("category") or "").strip() or None,
                "gender": (row.get("gender") or "").strip() or None,
            })
        except Exception as e:
            errors.append(f"Row {line_no}: {e}")

    if not products and not errors:
        errors.append("CSV has no products")

    return {"valid": not errors, "products": products, "errors": errors}

def products_to_csv(products: list[dict]) -> str:
    output = io.StringIO()
    fieldnames = ["brand", "model", "colorway", "size", "quantity", "price", "category", "gender"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for item in products:
        writer.writerow({name: item.get(name, "") for name in fieldnames})
    return output.getvalue()

def import_products(products: list[dict], replace: bool = False) -> int:
    ensure_app_tables()
    shop_id = get_default_shop_id()
    ph = db_placeholder()
    conn = get_db()
    try:
        if replace and USE_POSTGRES:
            conn.execute(f"DELETE FROM orders WHERE shop_id = {ph}", (shop_id,))
            conn.execute(f"DELETE FROM sneakers WHERE shop_id = {ph}", (shop_id,))
        elif replace:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM sneakers")

        for item in products:
            if USE_POSTGRES and replace:
                conn.execute(
                    f"""
                    INSERT INTO sneakers
                        (shop_id, brand, model, colorway, size, quantity, price, category, gender)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        shop_id,
                        item["brand"],
                        item["model"],
                        item["colorway"],
                        item["size"],
                        item["quantity"],
                        item["price"],
                        item["category"],
                        item["gender"],
                    ),
                )
            elif USE_POSTGRES:
                conn.execute(
                    f"""
                    DELETE FROM sneakers
                    WHERE shop_id = {ph}
                      AND LOWER(brand) = LOWER({ph})
                      AND LOWER(model) = LOWER({ph})
                      AND COALESCE(LOWER(colorway), '') = COALESCE(LOWER({ph}), '')
                      AND size = {ph}
                    """,
                    (shop_id, item["brand"], item["model"], item["colorway"], item["size"]),
                )
                conn.execute(
                    f"""
                    INSERT INTO sneakers
                        (shop_id, brand, model, colorway, size, quantity, price, category, gender)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        shop_id,
                        item["brand"],
                        item["model"],
                        item["colorway"],
                        item["size"],
                        item["quantity"],
                        item["price"],
                        item["category"],
                        item["gender"],
                    ),
                )
            else:
                conn.execute(
                    f"""
                    DELETE FROM sneakers
                    WHERE LOWER(brand) = LOWER({ph})
                      AND LOWER(model) = LOWER({ph})
                      AND COALESCE(LOWER(colorway), '') = COALESCE(LOWER({ph}), '')
                      AND size = {ph}
                    """,
                    (item["brand"], item["model"], item["colorway"], item["size"]),
                )
                conn.execute(
                    f"""
                    INSERT INTO sneakers
                        (brand, model, colorway, size, quantity, price, category, gender)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        item["brand"],
                        item["model"],
                        item["colorway"],
                        item["size"],
                        item["quantity"],
                        item["price"],
                        item["category"],
                        item["gender"],
                    ),
                )
        conn.commit()
        return len(products)
    finally:
        conn.close()

def replace_products(products: list[dict]) -> int:
    return import_products(products, replace=True)

def search_sneakers(query: str) -> list[dict]:
    """Поиск по складу — по бренду, модели, расцветке, категории"""
    words = [w for w in query.lower().split() if len(w) > 2]
    if not words:
        return []
    
    ph = db_placeholder()
    conditions = " OR ".join(
        [f"(LOWER(brand) LIKE {ph} OR LOWER(model) LIKE {ph} OR LOWER(colorway) LIKE {ph} OR LOWER(category) LIKE {ph})"
         for _ in words]
    )
    params = []
    for w in words:
        params.extend([f"%{w}%"] * 4)
    
    return fetch_all(
        f"SELECT * FROM sneakers WHERE {conditions} ORDER BY brand, model, size",
        params
    )

def extract_requested_size(query: str) -> float | None:
    match = re.search(r"\b(?:р(?:азмер)?\.?\s*)?([3-4][0-9](?:[.,]5)?)\b", query.lower())
    if not match:
        return None
    return float(match.group(1).replace(",", "."))

def get_relevant_sneakers(query: str, limit: int = 5) -> list[dict]:
    """RAG retrieval: достаём только самые похожие товары для промпта."""
    words = [w for w in re.findall(r"[\w-]+", query.lower()) if len(w) > 2]
    requested_size = extract_requested_size(query)
    if not words and requested_size is None:
        return []

    ph = db_placeholder()
    score_params: list = []
    where_params: list = []
    score_parts = []
    where_parts = []

    for word in words:
        like = f"%{word}%"
        score_parts.append(
            f"""
            CASE WHEN LOWER(brand) LIKE {ph} THEN 5 ELSE 0 END +
            CASE WHEN LOWER(model) LIKE {ph} THEN 4 ELSE 0 END +
            CASE WHEN LOWER(colorway) LIKE {ph} THEN 2 ELSE 0 END +
            CASE WHEN LOWER(category) LIKE {ph} THEN 1 ELSE 0 END +
            CASE WHEN LOWER(gender) LIKE {ph} THEN 1 ELSE 0 END
            """
        )
        score_params.extend([like, like, like, like, like])
        where_parts.append(
            f"(LOWER(brand) LIKE {ph} OR LOWER(model) LIKE {ph} OR LOWER(colorway) LIKE {ph} OR LOWER(category) LIKE {ph} OR LOWER(gender) LIKE {ph})"
        )
        where_params.extend([like, like, like, like, like])

    if requested_size is not None:
        score_parts.append(f"CASE WHEN size = {ph} THEN 6 ELSE 0 END")
        score_params.append(requested_size)
        if not words:
            where_parts.append(f"size = {ph}")
            where_params.append(requested_size)

    score_sql = " + ".join(score_parts) or "0"
    where_sql = " OR ".join(where_parts) or "1=1"
    params = [*score_params, *where_params]
    params.append(limit)

    return fetch_all(
        f"""
        SELECT *, ({score_sql}) AS relevance
        FROM sneakers
        WHERE ({where_sql}) AND quantity > 0
        ORDER BY relevance DESC, brand, model, size
        LIMIT {ph}
        """,
        params,
    )

def format_sneakers_context(items: list[dict]) -> str:
    if not items:
        return "Нет точных совпадений в наличии. Попроси уточнить бренд, модель, размер или стиль."

    lines = []
    for item in items:
        colorway = item.get("colorway") or ""
        category = item.get("category") or ""
        lines.append(
            f"{item['brand']} {item['model']} {colorway}|"
            f"размер {item['size']}|{item['price']}₸|"
            f"остаток {item['quantity']}|{category}"
        )
    return "\n".join(lines)

def check_availability(brand: str = "", model: str = "", size: float = None) -> list[dict]:
    """Точная проверка наличия"""
    conds, params = ["1=1"], []
    ph = db_placeholder()
    if brand:
        conds.append(f"LOWER(brand) LIKE {ph}")
        params.append(f"%{brand.lower()}%")
    if model:
        conds.append(f"LOWER(model) LIKE {ph}")
        params.append(f"%{model.lower()}%")
    if size:
        conds.append(f"size = {ph}")
        params.append(size)
    
    return fetch_all(
        f"SELECT * FROM sneakers WHERE {' AND '.join(conds)} ORDER BY size",
        params
    )

def get_db_summary() -> str:
    """
    ОПТИМИЗАЦИЯ 1: Компактный текстовый формат вместо JSON.
    Экономия ~61% токенов на описании склада.
    Формат: Бренд Модель | цена | размеры | наличие
    """
    if USE_POSTGRES:
        query = """
            SELECT brand, model, MIN(price) as price, category,
                   STRING_AGG(DISTINCT CAST(size::INTEGER AS TEXT), ',') as sizes,
                   SUM(quantity) as total_qty
            FROM sneakers
            GROUP BY brand, model, category
            ORDER BY brand, model
        """
    else:
        query = (
            "SELECT brand, model, MIN(price) as price, category, "
            "GROUP_CONCAT(DISTINCT CAST(size AS INTEGER)) as sizes, "
            "SUM(quantity) as total_qty "
            "FROM sneakers GROUP BY brand, model ORDER BY brand, model"
        )
    rows = fetch_all(query)
    lines = []
    for r in rows:
        stock = "есть" if r["total_qty"] > 0 else "нет"
        lines.append(f"{r['brand']} {r['model']}|{r['price']}₸|р.{r['sizes']}|{stock}")
    return "\n".join(lines)

# ОПТИМИЗАЦИЯ 2: Системный промпт — только суть, без лишних слов.
# Каждый лишний токен в system prompt умножается на КАЖДЫЙ запрос.
SYSTEM_PROMPT = """Консультант магазина кроссовок. Отвечай по-русски, 2-3 предложения максимум.

НАЙДЕНО НА СКЛАДЕ (бренд модель цвет|размер|цена|остаток|категория):
{product_context}

Правила: используй только найденные товары; цены в ₸; если точного совпадения нет — задай уточняющий вопрос или предложи ближайшие варианты; не выдумывай."""

# Хранилище истории чатов (в продакшне — Redis или БД)
chat_sessions: dict[str, list] = {}
redis_client = None

async def get_redis():
    global redis_client
    if not REDIS_URL or redis is None:
        return None
    if redis_client is None:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client

async def close_redis() -> None:
    global redis_client
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None

async def get_redis_status() -> dict:
    if not REDIS_URL:
        return {"redis": "disabled", "redis_ok": False}
    if redis is None:
        return {"redis": "missing_dependency", "redis_ok": False}
    try:
        client = await get_redis()
        await client.ping()
        return {"redis": "enabled", "redis_ok": True}
    except Exception as e:
        log.exception("Redis healthcheck failed")
        return {"redis": "enabled", "redis_ok": False, "redis_error": type(e).__name__}

async def check_rate_limit(user_id: str) -> tuple[bool, int]:
    client = await get_redis()
    if client is None:
        return check_memory_rate_limit(user_id)

    key = f"rate:{user_id}:{int(time.time() // RATE_LIMIT_WINDOW_SECONDS)}"
    try:
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, RATE_LIMIT_WINDOW_SECONDS + 5)
        remaining = max(0, RATE_LIMIT_MESSAGES - current)
        return current <= RATE_LIMIT_MESSAGES, remaining
    except Exception:
        log.exception("Redis rate limit failed")
        return check_memory_rate_limit(user_id)

memory_rate_limits: dict[str, tuple[int, int]] = {}

def check_memory_rate_limit(user_id: str) -> tuple[bool, int]:
    bucket = int(time.time() // RATE_LIMIT_WINDOW_SECONDS)
    key = f"{user_id}:{bucket}"
    count, _ = memory_rate_limits.get(key, (0, bucket))
    count += 1
    memory_rate_limits[key] = (count, bucket)
    remaining = max(0, RATE_LIMIT_MESSAGES - count)
    return count <= RATE_LIMIT_MESSAGES, remaining

async def save_session_message(user_id: str, role: str, content: str) -> None:
    message = json.dumps({"role": role, "content": content}, ensure_ascii=False)
    client = await get_redis()
    if client is None:
        chat_sessions.setdefault(user_id, []).append({"role": role, "content": content})
        chat_sessions[user_id] = chat_sessions[user_id][-6:]
        return

    key = f"session:{user_id}"
    try:
        await client.rpush(key, message)
        await client.ltrim(key, -6, -1)
        await client.expire(key, SESSION_TTL_SECONDS)
    except Exception:
        log.exception("Redis session write failed")
        chat_sessions.setdefault(user_id, []).append({"role": role, "content": content})
        chat_sessions[user_id] = chat_sessions[user_id][-6:]

async def load_session_history(user_id: str) -> list[dict]:
    client = await get_redis()
    if client is None:
        return chat_sessions.get(user_id, [])[-6:]

    key = f"session:{user_id}"
    try:
        raw_messages = await client.lrange(key, -6, -1)
        return [json.loads(item) for item in raw_messages]
    except Exception:
        log.exception("Redis session read failed")
        return chat_sessions.get(user_id, [])[-6:]

order_states: dict[str, dict] = {}

async def get_order_state(user_id: str) -> dict | None:
    try:
        client = await get_redis()
        if client is None:
            return order_states.get(user_id)

        raw_state = await client.get(f"order:{user_id}")
        return json.loads(raw_state) if raw_state else None
    except Exception as e:
        log.error(f"Get order state failed: {e}")
        return order_states.get(user_id)

async def set_order_state(user_id: str, state: dict) -> None:
    try:
        client = await get_redis()
        if client is None:
            order_states[user_id] = state
            return

        await client.set(f"order:{user_id}", json.dumps(state, ensure_ascii=False), ex=SESSION_TTL_SECONDS)
    except Exception as e:
        log.error(f"Set order state failed: {e}")
        order_states[user_id] = state

async def clear_order_state(user_id: str) -> None:
    try:
        client = await get_redis()
        if client is None:
            order_states.pop(user_id, None)
            return

        await client.delete(f"order:{user_id}")
    except Exception as e:
        log.error(f"Clear order state failed: {e}")
        order_states.pop(user_id, None)

def looks_like_order_request(message: str) -> bool:
    text = message.lower()
    triggers = [
        "хочу купить", "купить", "заказать", "оформить",
        "беру", "возьму", "оплатить", "заказ",
    ]
    return any(trigger in text for trigger in triggers)

def looks_like_phone(message: str) -> bool:
    digits = re.sub(r"\D", "", message)
    return 10 <= len(digits) <= 15

async def notify_manager(order_id: int | None, state: dict, channel: str, external_user_id: str) -> None:
    try:
        if not tg_bot or not MANAGER_TELEGRAM_CHAT_ID:
            return

        text = (
            "Новый заказ SoleBot\n"
            f"ID: {order_id or 'unknown'}\n"
            f"Канал: {channel}\n"
            f"Клиент: {external_user_id}\n"
            f"Имя: {state.get('name', '')}\n"
            f"Телефон: {state.get('phone', '')}\n"
            f"Интерес: {state.get('product_interest', '')}"
        )
        await tg_bot.send_message(MANAGER_TELEGRAM_CHAT_ID, text)
    except Exception as e:
        log.error(f"Manager notification failed: {e}")

async def handle_order_flow(user_id: str, user_message: str) -> str | None:
    try:
        channel, external_user_id = split_user_id(user_id)
        state = await get_order_state(user_id)

        if state is None:
            if not looks_like_order_request(user_message):
                return None

            await set_order_state(user_id, {
                "step": "name",
                "product_interest": user_message.strip(),
            })
            return "Отлично, оформим заказ. Напишите, пожалуйста, ваше имя."

        step = state.get("step")
        if step == "name":
            name = user_message.strip()
            if len(name) < 2:
                return "Напишите, пожалуйста, имя чуть подробнее."

            state["name"] = name
            state["step"] = "phone"
            await set_order_state(user_id, state)
            return "Спасибо. Теперь отправьте номер телефона для связи."

        if step == "phone":
            phone = user_message.strip()
            if not looks_like_phone(phone):
                return "Похоже, это не номер телефона. Отправьте номер в формате +7..."

            state["phone"] = phone
            order_id = create_order(
                channel,
                external_user_id,
                state.get("name", ""),
                state.get("phone", ""),
                state.get("product_interest", ""),
            )
            await notify_manager(order_id, state, channel, external_user_id)
            await clear_order_state(user_id)
            return "Заказ принят. Менеджер скоро свяжется с вами для подтверждения."

        await clear_order_state(user_id)
        return None
    except Exception as e:
        log.error(f"Order flow failed: {e}")
        return None

async def ask_ai(user_id: str, user_message: str) -> str:
    """
    Groq API — llama-3.1-8b-instant.
    Формат совместим с OpenAI: system идёт первым сообщением в messages[].
    История: последние 6 сообщений (3 пары) — экономия токенов.
    """
    if not user_message or not user_message.strip():
        return "Напишите, какую модель, размер или стиль кроссовок вы ищете."

    started_at = time.perf_counter()
    channel, external_user_id = split_user_id(user_id)
    conversation_id = None
    product_count = 0

    allowed, remaining = await check_rate_limit(user_id)
    if not allowed:
        reply = "Слишком много сообщений за минуту. Подождите немного и напишите снова."
        try:
            log_analytics_event(
                channel,
                "rate_limited",
                {
                    "user_message": user_message,
                    "limit": RATE_LIMIT_MESSAGES,
                    "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                },
            )
        except Exception:
            log.exception("Rate limit analytics failed")
        return reply

    try:
        conversation_id = get_or_create_conversation(channel, external_user_id)
        save_message(conversation_id, "user", user_message)
        await save_session_message(user_id, "user", user_message)
        history = await load_session_history(user_id)
        if not history:
            history = load_recent_messages(conversation_id, limit=6)
    except Exception:
        log.exception("Conversation storage failed")
        await save_session_message(user_id, "user", user_message)
        history = await load_session_history(user_id)

    order_reply = await handle_order_flow(user_id, user_message)
    if order_reply:
        await save_ai_result(user_id, conversation_id, channel, user_message, order_reply, started_at, product_count, "order")
        return order_reply

    try:
        relevant_items = get_relevant_sneakers(user_message, limit=5)
        product_count = len(relevant_items)
        product_context = format_sneakers_context(relevant_items)
    except Exception:
        log.exception("RAG retrieval failed")
        product_context = "Склад временно недоступен. Попроси клиента уточнить запрос или подождать немного."

    # Groq: system передаётся внутри messages как первый элемент
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(product_context=product_context)},
        *history,
    ]

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 250,
                    "temperature": 0.4,  # ниже дефолта — меньше "фантазий" про товары
                    "messages": messages,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error(f"Groq request failed: {e}")
            reply = fallback_reply(user_message)
            await save_ai_result(user_id, conversation_id, channel, user_message, reply, started_at, product_count, "fallback")
            return reply

    data = resp.json()
    usage = data.get("usage") or {}

    if "error" in data:
        log.error(f"Groq error: {data['error']}")
        reply = fallback_reply(user_message)
        await save_ai_result(user_id, conversation_id, channel, user_message, reply, started_at, product_count, "fallback", usage)
        return reply

    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "Ошибка, попробуйте позже.")

    await save_ai_result(user_id, conversation_id, channel, user_message, reply, started_at, product_count, "ai", usage)
    return reply

async def save_ai_result(
    user_id: str,
    conversation_id: int | None,
    channel: str,
    user_message: str,
    reply: str,
    started_at: float,
    product_count: int,
    mode: str,
    usage: dict | None = None,
) -> None:
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    usage = usage or {}
    try:
        if conversation_id is not None:
            save_message(conversation_id, "assistant", reply)
        else:
            chat_sessions.setdefault(user_id, []).append({"role": "assistant", "content": reply})
        await save_session_message(user_id, "assistant", reply)

        log_analytics_event(
            channel,
            "chat_reply",
            {
                "mode": mode,
                "user_message": user_message,
                "reply": reply,
                "latency_ms": latency_ms,
                "rag_products": product_count,
                "total_tokens": usage.get("total_tokens", 0),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
        )
    except Exception:
        log.exception("Saving AI result failed")

def fallback_reply(user_message: str) -> str:
    """Простой SQL-ответ, если ИИ временно недоступен."""
    try:
        items = search_sneakers(user_message)[:3]
    except Exception:
        log.exception("Fallback search failed")
        return "Сейчас не получается проверить склад. Напишите, пожалуйста, модель и размер — менеджер уточнит наличие."

    if not items:
        return "Сейчас не вижу точного совпадения на складе. Напишите бренд, модель или нужный размер — проверю по каталогу."

    lines = []
    for item in items:
        status = "есть" if item.get("quantity", 0) > 0 else "нет в наличии"
        lines.append(
            f"{item['brand']} {item['model']} {item.get('colorway') or ''}, "
            f"размер {item['size']}, {item['price']}₸ — {status}"
        )
    return "Нашёл по складу: " + "; ".join(lines)

# ─── Telegram ─────────────────────────────────────────────────────────
tg_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
tg_dp  = Dispatcher() if TELEGRAM_BOT_TOKEN else None

if tg_dp:
    @tg_dp.message(CommandStart())
    async def tg_start(msg: Message):
        await msg.answer(
            "Привет! Я SoleBot — ваш консультант по кроссовкам. 👟\n"
            "Спросите о любой модели — проверю наличие на складе!"
        )

    @tg_dp.message()
    async def tg_message(msg: Message):
        user_id = f"tg_{msg.from_user.id}"
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        reply = await ask_ai(user_id, msg.text or "")
        await msg.answer(reply)

# ─── WhatsApp ─────────────────────────────────────────────────────────
async def send_whatsapp(to: str, text: str):
    """Отправить сообщение через WhatsApp Business API"""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v18.0/{WHATSAPP_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text}
            }
        )

# ─── Instagram ────────────────────────────────────────────────────────
async def send_instagram(recipient_id: str, text: str):
    """Отправить сообщение через Instagram Messenger API"""
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": INSTAGRAM_TOKEN},
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": text}
            }
        )

# ─── FastAPI app ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_app_tables()
        log.info("Database app tables are ready")
    except Exception as e:
        log.error(f"Database app table setup failed: {e}")

    if tg_bot and TELEGRAM_WEBHOOK:
        try:
            webhook_url = TELEGRAM_WEBHOOK.rstrip("/") + "/tg/webhook"
            await tg_bot.set_webhook(webhook_url, drop_pending_updates=True)
            log.info(f"Telegram webhook установлен: {webhook_url}")
        except Exception as e:
            log.error(f"Ошибка установки webhook: {e}")
    yield
    await close_redis()
    if tg_bot:
        try:
            await tg_bot.session.close()
        except Exception:
            pass

app = FastAPI(title="SoleBot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Telegram webhook ──
@app.post("/tg/webhook")
async def telegram_webhook(request: Request):
    if not tg_bot:
        raise HTTPException(503, "Telegram не настроен")
    try:
        data = await request.json()
        update = types.Update.model_validate(data)
        await tg_dp.feed_update(tg_bot, update)
    except Exception as e:
        log.error(f"Telegram webhook processing failed: {e}")
        return {"ok": True, "ignored": True}
    return {"ok": True}

# ── WhatsApp webhook ──
@app.get("/wa/webhook")
async def whatsapp_verify(request: Request):
    """Meta требует верификацию webhook"""
    p = request.query_params
    if p.get("hub.verify_token") == WHATSAPP_VERIFY:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Неверный verify token")

@app.post("/wa/webhook")
async def whatsapp_message(request: Request):
    data = await request.json()
    try:
        entry    = data["entry"][0]
        change   = entry["changes"][0]["value"]
        msg      = change["messages"][0]
        phone    = msg["from"]
        text     = msg["text"]["body"]
        user_id  = f"wa_{phone}"
        
        reply = await ask_ai(user_id, text)
        await send_whatsapp(phone, reply)
    except (KeyError, IndexError):
        pass  # не все события содержат сообщения
    return {"status": "ok"}

# ── Instagram webhook ──
@app.get("/ig/webhook")
async def instagram_verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == INSTAGRAM_VERIFY:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Неверный verify token")

@app.post("/ig/webhook")
async def instagram_message(request: Request):
    data = await request.json()
    try:
        entry    = data["entry"][0]
        msg      = entry["messaging"][0]
        sender   = msg["sender"]["id"]
        text     = msg["message"]["text"]
        user_id  = f"ig_{sender}"
        
        reply = await ask_ai(user_id, text)
        await send_instagram(sender, reply)
    except (KeyError, IndexError):
        pass
    return {"status": "ok"}

# ── Web Chat API (для sneaker_bot.html) ──
@app.post("/api/chat")
async def web_chat(request: Request):
    data = await request.json()
    session_id = data.get("session_id", "web_anon")
    text       = data.get("message", "")
    user_id    = f"web_{session_id}"
    
    reply = await ask_ai(user_id, text)
    return {"reply": reply}

# ── Healthcheck ──
@app.get("/")
async def health():
    db_status = get_database_status()
    redis_status = await get_redis_status()
    return {
        "status": "ok",
        **db_status,
        **redis_status,
        "channels": {
            "telegram":  bool(TELEGRAM_BOT_TOKEN),
            "whatsapp":  bool(WHATSAPP_TOKEN),
            "instagram": bool(INSTAGRAM_TOKEN),
            "web":       True,
        }
    }

@app.get("/admin/stats")
async def admin_stats(request: Request):
    require_admin(request)

    return {
        "database": "postgresql" if USE_POSTGRES else "sqlite",
        "sneakers": count_rows("sneakers"),
        "orders": count_rows("orders"),
        "conversations": count_rows("conversations"),
        "messages": count_rows("messages"),
        "analytics_events": count_rows("analytics_events"),
        "total_tokens": count_logged_tokens(),
    }

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    require_admin(request)
    return HTMLResponse(render_admin_page(request.query_params.get("token", "")))

@app.get("/admin/products")
async def admin_products(request: Request, limit: int = 100, offset: int = 0):
    require_admin(request)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return {
        "count": count_rows("sneakers"),
        "limit": limit,
        "offset": offset,
        "items": list_products(limit=limit, offset=offset),
    }

@app.get("/admin/orders")
async def admin_orders(request: Request, limit: int = 100, offset: int = 0):
    require_admin(request)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return {
        "count": count_rows("orders"),
        "limit": limit,
        "offset": offset,
        "items": list_orders(limit=limit, offset=offset),
    }

@app.get("/admin/import-template")
async def admin_import_template(request: Request):
    require_admin(request)
    csv_text = (
        "brand,model,colorway,size,quantity,price,category,gender\n"
        "Nike,Air Force 1,White/White,42,7,45000,lifestyle,unisex\n"
        "Adidas,Samba OG,White/Black,43,4,52000,lifestyle,unisex\n"
    )
    return PlainTextResponse(csv_text, media_type="text/csv")

@app.get("/admin/export")
async def admin_export(request: Request):
    require_admin(request)
    csv_text = products_to_csv(list_products(limit=10000))
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=solebot-products.csv"},
    )

@app.post("/admin/import-preview")
async def admin_import_preview(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")

    content = await file.read()
    result = validate_product_csv(content)
    preview = result["products"][:10]
    return {
        "filename": file.filename,
        "valid": result["valid"],
        "valid_rows": len(result["products"]),
        "error_count": len(result["errors"]),
        "errors": result["errors"][:50],
        "preview": preview,
        "next_step": "If valid is true, upload the same file to /admin/import to replace the catalog.",
    }

@app.post("/admin/import")
async def admin_import(request: Request, file: UploadFile = File(...), replace: bool = False):
    require_admin(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")

    try:
        content = await file.read()
        result = validate_product_csv(content)
        if not result["valid"]:
            raise ValueError("; ".join(result["errors"][:10]))
        products = result["products"]
        imported = import_products(products, replace=replace)
        log_analytics_event(
            "admin",
            "products_imported",
            {"filename": file.filename, "imported": imported, "replace": replace},
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return {
        "status": "ok",
        "mode": "replace" if replace else "update",
        "imported": imported,
        "total_products": count_rows("sneakers"),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
