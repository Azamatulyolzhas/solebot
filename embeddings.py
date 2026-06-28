"""
Vector embeddings for semantic product search (RAG).

Backend: a hosted, OpenAI-compatible /v1/embeddings endpoint (OpenAI by default;
point EMBEDDING_BASE_URL/EMBEDDING_MODEL at Jina/Gemini/etc.). Chosen over a local
sentence-transformers model so the Railway image stays light (no PyTorch) and the
embedding quota is separate from the Groq chat quota that gets rate-limited.

Active only when USE_POSTGRES is on AND EMBEDDING_API_KEY is set. The public
interface (product_text / embed_text / embed_products / is_available) is unchanged,
so vector_search_products, _update_embeddings and backfill_embeddings.py keep working.
"""
import json
import logging

import httpx

from config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    USE_POSTGRES,
)

log = logging.getLogger(__name__)

_HTTP_TIMEOUT = 20.0
_dim_warned = False


def is_available() -> bool:
    """True only if pgvector (Postgres) and a hosted embedding key are configured."""
    return bool(USE_POSTGRES and EMBEDDING_API_KEY)


def product_text(product: dict) -> str:
    """Build a single text string from product fields for embedding."""
    parts = []
    if product.get("name"):
        parts.append(product["name"])
    if product.get("category"):
        parts.append(product["category"])
    if product.get("description"):
        parts.append(product["description"])
    attrs = product.get("attributes") or {}
    if attrs:
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}
        for k, v in attrs.items():
            parts.append(f"{k} {v}")
    return " ".join(parts)


def _embed(texts: list[str]) -> list[list[float] | None]:
    """Call the hosted embeddings API for a batch. Returns a list aligned with the
    input; any failure degrades to all-None so callers fall back to keyword search
    rather than crash. A blank input is sent as a single space (the API rejects '')."""
    global _dim_warned
    if not is_available() or not texts:
        return [None] * len(texts)

    payload = {
        "model": EMBEDDING_MODEL,
        "input": [t if (t and t.strip()) else " " for t in texts],
        # text-embedding-3-* honour this and shorten the vector to match our
        # pgvector(384) column. Providers that ignore it must return EMBEDDING_DIM.
        "dimensions": EMBEDDING_DIM,
    }
    headers = {
        "Authorization": f"Bearer {EMBEDDING_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(
            f"{EMBEDDING_BASE_URL}/embeddings",
            headers=headers,
            json=payload,
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.error("Embedding API call failed: %s", e)
        return [None] * len(texts)

    data = resp.json().get("data") or []
    # The API returns rows with an `index`; sort by it so vectors realign with input.
    rows = sorted(data, key=lambda r: r.get("index", 0))
    if len(rows) != len(texts):
        log.error("Embedding API returned %d rows for %d inputs", len(rows), len(texts))
        return [None] * len(texts)

    out: list[list[float] | None] = []
    for r in rows:
        vec = r.get("embedding")
        if not isinstance(vec, list):
            out.append(None)
            continue
        if len(vec) != EMBEDDING_DIM:
            if not _dim_warned:
                log.error(
                    "Embedding dim mismatch: got %d, expected %d (EMBEDDING_DIM / "
                    "pgvector column). Set EMBEDDING_DIM to the model's native size "
                    "and migrate the column, or use a model that supports `dimensions`.",
                    len(vec), EMBEDDING_DIM,
                )
                _dim_warned = True
            out.append(None)
            continue
        out.append(vec)
    return out


def embed_text(text: str) -> list[float] | None:
    return _embed([text])[0]


def embed_products(products: list[dict]) -> list[list[float] | None]:
    """Batch embed multiple products. Returns list aligned with input."""
    return _embed([product_text(p) for p in products])
