import json
import logging

from config import USE_POSTGRES
from db import get_db

log = logging.getLogger(__name__)

SHOP_EXTRA_COLUMNS = (
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS tg_token TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS tg_webhook_secret TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS groq_system_prompt TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_email TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_password_hash TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS moysklad_token TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS sync_api_key TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS bot_role TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS business_type TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS website_url TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS data_source TEXT DEFAULT 'manual'",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS groq_api_key TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_telegram_chat_id TEXT",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_telegram_username TEXT",
)

SUBSCRIPTION_EXTRA_COLUMNS = (
    "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS period_starts_at TIMESTAMPTZ",
)


def _table_exists(conn, table: str) -> bool:
    if USE_POSTGRES:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def migrate_sneakers_to_products(conn) -> None:
    """One-time copy sneakers → products preserving IDs. Safe to re-run."""
    if not _table_exists(conn, "sneakers"):
        return
    if not _table_exists(conn, "products"):
        return

    if USE_POSTGRES:
        conn.execute("""
            INSERT INTO products (id, shop_id, name, description, sku, category, price, quantity, attributes)
            SELECT
                s.id,
                s.shop_id,
                TRIM(COALESCE(s.brand, '') || ' ' || COALESCE(s.model, '')),
                NULL,
                NULL,
                s.category,
                s.price,
                s.quantity,
                jsonb_build_object(
                    'size', s.size,
                    'colorway', s.colorway,
                    'gender', s.gender,
                    'brand', s.brand,
                    'model', s.model
                )
            FROM sneakers s
            WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = s.id)
        """)
        conn.execute("""
            UPDATE orders
            SET product_id = sneaker_id
            WHERE product_id IS NULL AND sneaker_id IS NOT NULL
        """)
    else:
        rows = conn.execute("SELECT * FROM sneakers").fetchall()
        for row in rows:
            r = dict(row)
            name = f"{r.get('brand') or ''} {r.get('model') or ''}".strip()
            attrs = {
                k: r[k]
                for k in ("size", "colorway", "gender", "brand", "model")
                if r.get(k) is not None
            }
            existing = conn.execute(
                "SELECT 1 FROM products WHERE id = ?",
                (r["id"],),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO products (id, shop_id, name, description, sku, category, price, quantity, attributes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["id"],
                    r.get("shop_id"),
                    name,
                    None,
                    None,
                    r.get("category"),
                    r.get("price", 0),
                    r.get("quantity", 0),
                    json.dumps(attrs, ensure_ascii=False),
                ),
            )
        conn.execute("""
            UPDATE orders SET product_id = sneaker_id
            WHERE product_id IS NULL AND sneaker_id IS NOT NULL
        """)

    migrated = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()
    count = migrated["c"] if isinstance(migrated, dict) else migrated[0]
    log.info("Products catalog ready: %s rows", count)


def drop_legacy_sneakers_table(conn) -> None:
    """Drop legacy sneakers table after data lives in products."""
    if not _table_exists(conn, "sneakers"):
        return

    if USE_POSTGRES:
        fks = conn.execute(
            """
            SELECT conrelid::regclass::text AS table_name, conname
            FROM pg_constraint
            WHERE confrelid = 'sneakers'::regclass AND contype = 'f'
            """
        ).fetchall()
        for fk in fks:
            tbl = fk["table_name"] if isinstance(fk, dict) else fk[0]
            name = fk["conname"] if isinstance(fk, dict) else fk[1]
            conn.execute(f'ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS "{name}"')
        conn.execute("DROP TABLE IF EXISTS sneakers CASCADE")
        conn.execute("ALTER TABLE orders DROP COLUMN IF EXISTS sneaker_id")
    else:
        conn.execute("DROP TABLE IF EXISTS sneakers")
        order_cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
        if "sneaker_id" in order_cols:
            try:
                conn.execute("ALTER TABLE orders DROP COLUMN sneaker_id")
            except Exception:
                log.warning("Could not drop orders.sneaker_id on SQLite")

    log.info("Legacy sneakers table removed")


def ensure_app_tables() -> None:
    conn = get_db()
    try:
        if USE_POSTGRES:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shops (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    tg_token TEXT,
                    tg_webhook_secret TEXT,
                    groq_system_prompt TEXT,
                    owner_email TEXT,
                    owner_password_hash TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            for ddl in SHOP_EXTRA_COLUMNS:
                conn.execute(ddl)
            for ddl in SUBSCRIPTION_EXTRA_COLUMNS:
                conn.execute(ddl)
            conn.execute("""
                UPDATE subscriptions
                SET period_starts_at = created_at
                WHERE period_starts_at IS NULL AND created_at IS NOT NULL
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id BIGSERIAL PRIMARY KEY,
                    shop_id BIGINT REFERENCES shops(id),
                    name TEXT NOT NULL,
                    description TEXT,
                    sku TEXT,
                    category TEXT,
                    price INTEGER NOT NULL DEFAULT 0,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id BIGSERIAL PRIMARY KEY,
                    shop_id BIGINT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
                    plan TEXT NOT NULL DEFAULT 'trial',
                    status TEXT NOT NULL DEFAULT 'active',
                    messages_limit INT NOT NULL DEFAULT 500,
                    channels_limit INT NOT NULL DEFAULT 1,
                    trial_ends_at TIMESTAMPTZ,
                    period_ends_at TIMESTAMPTZ,
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
                    product_id BIGINT REFERENCES products(id),
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
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_id BIGINT REFERENCES products(id)",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS channel TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_user_id TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_interest TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new'",
            ):
                conn.execute(ddl)
            conn.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS shop_id BIGINT REFERENCES shops(id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_sku ON products(shop_id, sku)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_shop ON orders(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_shop ON conversations(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_shop ON subscriptions(shop_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id BIGSERIAL PRIMARY KEY,
                    shop_id BIGINT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
                    token TEXT NOT NULL UNIQUE,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    tg_token TEXT,
                    tg_webhook_secret TEXT,
                    groq_system_prompt TEXT,
                    owner_email TEXT,
                    owner_password_hash TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            existing_shop_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(shops)").fetchall()
            }
            sqlite_shop_cols = {
                "tg_token": "ALTER TABLE shops ADD COLUMN tg_token TEXT",
                "tg_webhook_secret": "ALTER TABLE shops ADD COLUMN tg_webhook_secret TEXT",
                "groq_system_prompt": "ALTER TABLE shops ADD COLUMN groq_system_prompt TEXT",
                "owner_email": "ALTER TABLE shops ADD COLUMN owner_email TEXT",
                "owner_password_hash": "ALTER TABLE shops ADD COLUMN owner_password_hash TEXT",
                "status": "ALTER TABLE shops ADD COLUMN status TEXT DEFAULT 'active'",
                "moysklad_token": "ALTER TABLE shops ADD COLUMN moysklad_token TEXT",
                "sync_api_key": "ALTER TABLE shops ADD COLUMN sync_api_key TEXT",
                "bot_role": "ALTER TABLE shops ADD COLUMN bot_role TEXT",
                "business_type": "ALTER TABLE shops ADD COLUMN business_type TEXT",
                "website_url": "ALTER TABLE shops ADD COLUMN website_url TEXT",
                "data_source": "ALTER TABLE shops ADD COLUMN data_source TEXT DEFAULT 'manual'",
                "groq_api_key": "ALTER TABLE shops ADD COLUMN groq_api_key TEXT",
                "owner_telegram_chat_id": "ALTER TABLE shops ADD COLUMN owner_telegram_chat_id TEXT",
                "owner_telegram_username": "ALTER TABLE shops ADD COLUMN owner_telegram_username TEXT",
            }
            for column, ddl in sqlite_shop_cols.items():
                if column not in existing_shop_columns:
                    conn.execute(ddl)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER,
                    name TEXT NOT NULL,
                    description TEXT,
                    sku TEXT,
                    category TEXT,
                    price INTEGER NOT NULL DEFAULT 0,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    attributes TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER NOT NULL,
                    plan TEXT NOT NULL DEFAULT 'trial',
                    status TEXT NOT NULL DEFAULT 'active',
                    messages_limit INTEGER NOT NULL DEFAULT 500,
                    channels_limit INTEGER NOT NULL DEFAULT 1,
                    trial_ends_at TIMESTAMP,
                    period_ends_at TIMESTAMP,
                    period_starts_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            sub_cols = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()}
            if "period_starts_at" not in sub_cols:
                conn.execute("ALTER TABLE subscriptions ADD COLUMN period_starts_at TIMESTAMP")
            conn.execute("""
                UPDATE subscriptions
                SET period_starts_at = created_at
                WHERE period_starts_at IS NULL AND created_at IS NOT NULL
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
                    product_id INTEGER,
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
                "product_id": "ALTER TABLE orders ADD COLUMN product_id INTEGER",
                "channel": "ALTER TABLE orders ADD COLUMN channel TEXT",
                "external_user_id": "ALTER TABLE orders ADD COLUMN external_user_id TEXT",
                "product_interest": "ALTER TABLE orders ADD COLUMN product_interest TEXT",
                "status": "ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'new'",
            }.items():
                if column not in existing_order_columns:
                    conn.execute(ddl)
            existing_conversation_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "shop_id" not in existing_conversation_columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN shop_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_shop ON orders(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_shop ON conversations(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_shop ON subscriptions(shop_id)")

        migrate_sneakers_to_products(conn)
        drop_legacy_sneakers_table(conn)
        conn.commit()
    finally:
        conn.close()
