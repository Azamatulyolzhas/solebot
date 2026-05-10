import os
import sqlite3

import psycopg
from dotenv import load_dotenv


load_dotenv()

SQLITE_PATH = os.getenv("DB_PATH", "sneakers.db")
DATABASE_URL = os.getenv("DATABASE_URL")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shops (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sneakers (
    id BIGSERIAL PRIMARY KEY,
    shop_id BIGINT REFERENCES shops(id),
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    colorway TEXT,
    size NUMERIC(4, 1) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    price INTEGER NOT NULL,
    category TEXT,
    gender TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sneakers_shop_brand_model
    ON sneakers (shop_id, brand, model);

CREATE INDEX IF NOT EXISTS idx_sneakers_search
    ON sneakers USING GIN (
        to_tsvector(
            'simple',
            coalesce(brand, '') || ' ' ||
            coalesce(model, '') || ' ' ||
            coalesce(colorway, '') || ' ' ||
            coalesce(category, '')
        )
    );

CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    shop_id BIGINT REFERENCES shops(id),
    sneaker_id BIGINT REFERENCES sneakers(id),
    customer_name TEXT,
    customer_phone TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id BIGSERIAL PRIMARY KEY,
    shop_id BIGINT REFERENCES shops(id),
    channel TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (shop_id, channel, external_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS analytics_events (
    id BIGSERIAL PRIMARY KEY,
    shop_id BIGINT REFERENCES shops(id),
    channel TEXT,
    event_name TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def main() -> None:
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is required. Add it to .env or Railway variables.")

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    sneakers = sqlite_conn.execute("SELECT * FROM sneakers ORDER BY id").fetchall()
    orders = sqlite_conn.execute("SELECT * FROM orders ORDER BY id").fetchall()

    with psycopg.connect(DATABASE_URL) as pg_conn:
        with pg_conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                INSERT INTO shops (name, slug)
                VALUES (%s, %s)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                ("Default shop", "default"),
            )
            shop_id = cur.fetchone()[0]

            cur.execute("DELETE FROM orders WHERE shop_id = %s", (shop_id,))
            cur.execute("DELETE FROM sneakers WHERE shop_id = %s", (shop_id,))

            sneaker_id_map = {}
            for row in sneakers:
                cur.execute(
                    """
                    INSERT INTO sneakers
                        (shop_id, brand, model, colorway, size, quantity, price, category, gender)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        shop_id,
                        row["brand"],
                        row["model"],
                        row["colorway"],
                        row["size"],
                        row["quantity"],
                        row["price"],
                        row["category"],
                        row["gender"],
                    ),
                )
                sneaker_id_map[row["id"]] = cur.fetchone()[0]

            for row in orders:
                cur.execute(
                    """
                    INSERT INTO orders (shop_id, sneaker_id, customer_name, customer_phone, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        shop_id,
                        sneaker_id_map.get(row["sneaker_id"]),
                        row["customer_name"],
                        row["customer_phone"],
                        row["created_at"],
                    ),
                )

        pg_conn.commit()

    sqlite_conn.close()
    print(f"Migrated {len(sneakers)} sneakers and {len(orders)} orders to PostgreSQL.")


if __name__ == "__main__":
    main()
