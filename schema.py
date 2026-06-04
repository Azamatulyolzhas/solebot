from config import USE_POSTGRES
from db import get_db


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
            for ddl in (
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS tg_token TEXT",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS tg_webhook_secret TEXT",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS groq_system_prompt TEXT",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_email TEXT",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_password_hash TEXT",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS moysklad_token TEXT",
                "ALTER TABLE shops ADD COLUMN IF NOT EXISTS sync_api_key TEXT",
            ):
                conn.execute(ddl)
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
            conn.execute("ALTER TABLE sneakers ADD COLUMN IF NOT EXISTS shop_id BIGINT REFERENCES shops(id)")
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
            conn.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS shop_id BIGINT REFERENCES shops(id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sneakers_shop ON sneakers(shop_id)")
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
            for column, ddl in {
                "tg_token": "ALTER TABLE shops ADD COLUMN tg_token TEXT",
                "tg_webhook_secret": "ALTER TABLE shops ADD COLUMN tg_webhook_secret TEXT",
                "groq_system_prompt": "ALTER TABLE shops ADD COLUMN groq_system_prompt TEXT",
                "owner_email": "ALTER TABLE shops ADD COLUMN owner_email TEXT",
                "owner_password_hash": "ALTER TABLE shops ADD COLUMN owner_password_hash TEXT",
                "status": "ALTER TABLE shops ADD COLUMN status TEXT DEFAULT 'active'",
            }.items():
                if column not in existing_shop_columns:
                    conn.execute(ddl)
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            existing_sneaker_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(sneakers)").fetchall()
            }
            if "shop_id" not in existing_sneaker_columns:
                conn.execute("ALTER TABLE sneakers ADD COLUMN shop_id INTEGER")
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
            existing_conversation_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "shop_id" not in existing_conversation_columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN shop_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sneakers_shop ON sneakers(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_shop ON orders(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_shop ON conversations(shop_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_shop ON subscriptions(shop_id)")
        conn.commit()
    finally:
        conn.close()
