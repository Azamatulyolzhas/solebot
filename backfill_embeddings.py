"""
One-time script: generate embeddings for all products that don't have one yet.
Run after deploying vector search:
    python backfill_embeddings.py
"""
import logging
from db import fetch_all, execute_write, get_db
from embeddings import embed_products, is_available, product_text
from schema import ensure_app_tables

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BATCH = 100


def backfill() -> None:
    if not is_available():
        log.error("Vector search not available (need USE_POSTGRES + pgvector + EMBEDDING_API_KEY)")
        return

    ensure_app_tables()

    total = 0
    offset = 0
    while True:
        rows = fetch_all(
            "SELECT id, name, description, sku, category, price, quantity, attributes "
            "FROM products WHERE embedding IS NULL "
            "ORDER BY id LIMIT %s OFFSET %s",
            (BATCH, offset),
        )
        if not rows:
            break

        vecs = embed_products(rows)
        conn = get_db()
        try:
            for row, vec in zip(rows, vecs):
                if vec is None:
                    continue
                conn.execute(
                    "UPDATE products SET embedding = %s::vector WHERE id = %s",
                    (str(vec), row["id"]),
                )
            conn.commit()
        finally:
            conn.close()

        total += len(rows)
        log.info("Embedded %d products so far...", total)
        offset += BATCH

    log.info("Done. Total embedded: %d", total)


if __name__ == "__main__":
    backfill()
