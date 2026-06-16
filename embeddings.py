"""
Vector embeddings for semantic product search.
Uses sentence-transformers (free, local, supports Russian).
Active only when USE_POSTGRES=true and pgvector is installed.
"""
import json
import logging

from config import USE_POSTGRES

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(EMBEDDING_MODEL)
            log.info("Embedding model loaded: %s", EMBEDDING_MODEL)
        except ImportError:
            log.warning("sentence-transformers not installed — vector search disabled")
            return None
    return _model


def is_available() -> bool:
    """True only if pgvector + sentence-transformers are usable."""
    if not USE_POSTGRES:
        return False
    return _get_model() is not None


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


def embed_text(text: str) -> list[float] | None:
    model = _get_model()
    if model is None:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except Exception:
        log.exception("embed_text failed")
        return None


def embed_products(products: list[dict]) -> list[list[float] | None]:
    """Batch embed multiple products. Returns list aligned with input."""
    model = _get_model()
    if model is None:
        return [None] * len(products)
    try:
        texts = [product_text(p) for p in products]
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=64)
        return [v.tolist() for v in vecs]
    except Exception:
        log.exception("embed_products batch failed")
        return [None] * len(products)
