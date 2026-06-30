import csv
import difflib
import io
import json
import re
import logging

from config import USE_POSTGRES
from db import db_placeholder, execute_write, fetch_all, get_db
from schema import ensure_app_tables
from shops import resolve_shop_id

log = logging.getLogger(__name__)

CSV_FIELDS = ["name", "description", "sku", "category", "price", "quantity", "attributes"]
REQUIRED_CSV = {"name", "price", "quantity"}
# Columns the importer reads directly. ANY other column in an uploaded CSV is folded
# into `attributes` under its header name (wide format) — so a shop can fill friendly
# per-attribute columns (бренд, пол, материал, размер, цвет, назначение, сезон…) in
# Excel instead of packing everything into one `attributes` cell. Stays universal:
# nothing here hardcodes which extra columns a given vertical uses.
_BASE_CSV_FIELDS = {"name", "description", "sku", "category", "price", "quantity", "attributes"}
# Attribute columns surfaced first (in this order) when EXPORTING the catalog to CSV.
_EXPORT_ATTR_PRIORITY = ["size", "color", "размер", "цвет"]

STOP_WORDS = {
    "есть", "ли", "какие", "какой", "какая", "какое", "хочу", "нужны", "нужен",
    "можно", "подскажи", "скажи", "покажи", "что", "это", "мне", "для", "вы",
    "нет", "как", "про", "по", "на", "из", "у", "вас", "вам", "меня", "этого",
    "занимаюсь", "занимается", "который", "которая", "которые",
    # Particles/filler. Short ones are dangerous as LIKE substrings — 'не' alone
    # matched 'коричНЕвый' and pulled brown products into an order (confirm step).
    "не", "же", "бы", "ещё", "еще", "вот", "ну", "уже", "там", "тут",
}

# Words that signal "just show me everything" rather than a specific need. A query
# counts as browse ONLY when every meaningful word is in here (see is_browse_query):
# 'покажи товары' → browse, but 'товары для футбола' → search. Greetings/stopwords
# like 'покажи', 'что', 'есть' are stripped as STOP_WORDS before the check.
BROWSE_VOCAB = {
    "каталог", "ассортимент", "товар", "товары", "catalog", "products",
    "весь", "вся", "всё", "все", "all",
}

CATALOG_CHAR_LIMIT = 3000
SKU_DETAIL_LIMIT = 20


def _parse_attributes(raw) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _attrs_json(attrs: dict | None) -> str:
    return json.dumps(attrs or {}, ensure_ascii=False, sort_keys=True)


def _normalize_row(row: dict) -> dict:
    attrs = _parse_attributes(row.get("attributes"))
    return {
        "id": row.get("id"),
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "sku": row.get("sku") or "",
        "category": row.get("category") or "",
        "price": row.get("price") or 0,
        "quantity": row.get("quantity") or 0,
        "attributes": attrs,
    }


def list_products(limit: int = 100, offset: int = 0, shop_id: int | None = None) -> list[dict]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    rows = fetch_all(
        f"""
        SELECT id, name, description, sku, category, price, quantity, attributes
        FROM products
        WHERE shop_id = {ph}
        ORDER BY name, sku, id
        LIMIT {ph} OFFSET {ph}
        """,
        (shop_id, limit, offset),
    )
    return [_normalize_row(r) for r in rows]


def _coerce_attr_value(val: str):
    """'42' -> 42, '8.5' -> 8.5, 'кожа' -> 'кожа'. One place so packed `attributes`
    cells and wide per-attribute columns coerce values identically."""
    val = str(val).strip()
    try:
        return float(val.replace(",", ".")) if "." in val else int(val)
    except ValueError:
        return val


def _parse_attributes_cell(value: str | None) -> dict:
    if not value or not str(value).strip():
        return {}
    text = str(value).strip()
    if text.startswith("{"):
        return _parse_attributes(text)
    attrs: dict = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, val = part.split(":", 1)
        key = key.strip()
        if not key:
            continue
        attrs[key] = _coerce_attr_value(val)
    return attrs


def _parse_int_cell(value, field: str = "value") -> int:
    """Parse CSV numeric cell; Excel often exports integers as '13990.0'."""
    if value is None or str(value).strip() == "":
        return 0
    text = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        return int(float(text))
    except ValueError as e:
        raise ValueError(f"{field} must be a number") from e


def _row_from_csv(row: dict, line_no: int) -> dict:
    name = (row.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")

    price = _parse_int_cell(row.get("price", 0), "price")
    quantity = _parse_int_cell(row.get("quantity", 0), "quantity")
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    if price <= 0:
        raise ValueError("price must be greater than 0")

    attrs = _parse_attributes_cell(row.get("attributes"))
    # Wide format: fold every non-base column into attributes under its header name.
    # A flat column wins over the same key inside a packed `attributes` cell.
    for col, val in row.items():
        if not col or col.strip() in _BASE_CSV_FIELDS:
            continue
        if val is None or str(val).strip() == "":
            continue
        attrs[col.strip()] = _coerce_attr_value(val)

    return {
        "name": name,
        "description": (row.get("description") or "").strip() or None,
        "sku": (row.get("sku") or "").strip() or None,
        "category": (row.get("category") or "").strip() or None,
        "price": price,
        "quantity": quantity,
        "attributes": attrs,
    }


def parse_product_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    missing = REQUIRED_CSV - headers
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

    products = []
    for line_no, row in enumerate(reader, start=2):
        try:
            products.append(_row_from_csv(row, line_no))
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
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    missing = REQUIRED_CSV - headers
    errors = []
    products = []

    if missing:
        errors.append(f"Missing columns: {', '.join(sorted(missing))}")
        return {"valid": False, "products": [], "errors": errors}

    for line_no, row in enumerate(reader, start=2):
        try:
            products.append(_row_from_csv(row, line_no))
        except Exception as e:
            errors.append(f"Row {line_no}: {e}")

    if not products and not errors:
        errors.append("CSV has no products")

    return {"valid": not errors, "products": products, "errors": errors}


def products_to_csv(products: list[dict]) -> str:
    """Export catalog as a WIDE CSV: base columns + one column per attribute key in
    use (priority keys first, the rest alphabetical). Round-trips with the importer,
    which folds those extra columns back into attributes."""
    base = ["name", "description", "sku", "category", "price", "quantity"]
    seen: list[str] = []
    for p in products:
        for k in (p.get("attributes") or {}):
            if k not in seen:
                seen.append(k)
    ordered = [k for k in _EXPORT_ATTR_PRIORITY if k in seen] + sorted(
        k for k in seen if k not in _EXPORT_ATTR_PRIORITY
    )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=base + ordered, lineterminator="\n")
    writer.writeheader()
    for item in products:
        attrs = item.get("attributes") or {}
        row = {
            "name": item.get("name", ""),
            "description": item.get("description") or "",
            "sku": item.get("sku") or "",
            "category": item.get("category") or "",
            "price": item.get("price", 0),
            "quantity": item.get("quantity", 0),
        }
        for k in ordered:
            v = attrs.get(k, "")
            row[k] = "" if v is None else v
        writer.writerow(row)
    return output.getvalue()


def _delete_existing(conn, shop_id: int, item: dict) -> None:
    ph = db_placeholder()
    sku = (item.get("sku") or "").strip()
    attrs_json = _attrs_json(item.get("attributes"))

    if sku:
        conn.execute(
            f"DELETE FROM products WHERE shop_id = {ph} AND LOWER(sku) = LOWER({ph})",
            (shop_id, sku),
        )
        return

    if USE_POSTGRES:
        conn.execute(
            f"""
            DELETE FROM products
            WHERE shop_id = {ph}
              AND LOWER(name) = LOWER({ph})
              AND COALESCE(attributes::text, '{{}}') = {ph}
            """,
            (shop_id, item["name"], attrs_json),
        )
    else:
        conn.execute(
            f"""
            DELETE FROM products
            WHERE shop_id = {ph}
              AND LOWER(name) = LOWER({ph})
              AND COALESCE(attributes, '{{}}') = {ph}
            """,
            (shop_id, item["name"], attrs_json),
        )


def _insert_product(conn, shop_id: int, item: dict) -> None:
    ph = db_placeholder()
    attrs_json = _attrs_json(item.get("attributes"))
    if USE_POSTGRES:
        conn.execute(
            f"""
            INSERT INTO products
                (shop_id, name, description, sku, category, price, quantity, attributes)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}::jsonb)
            """,
            (
                shop_id,
                item["name"],
                item.get("description"),
                item.get("sku"),
                item.get("category"),
                item["price"],
                item["quantity"],
                attrs_json,
            ),
        )
    else:
        conn.execute(
            f"""
            INSERT INTO products
                (shop_id, name, description, sku, category, price, quantity, attributes)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """,
            (
                shop_id,
                item["name"],
                item.get("description"),
                item.get("sku"),
                item.get("category"),
                item["price"],
                item["quantity"],
                attrs_json,
            ),
        )


def _update_embeddings(conn, shop_id: int, products: list[dict]) -> None:
    """Write embeddings for newly inserted products. Runs after commit."""
    from embeddings import embed_products, is_available
    if not is_available():
        return
    try:
        vecs = embed_products(products)
        for product, vec in zip(products, vecs):
            if vec is None:
                continue
            sku = (product.get("sku") or "").strip()
            name = product.get("name") or ""
            if sku:
                conn.execute(
                    "UPDATE products SET embedding = %s::vector "
                    "WHERE shop_id = %s AND LOWER(sku) = LOWER(%s)",
                    (str(vec), shop_id, sku),
                )
            else:
                conn.execute(
                    "UPDATE products SET embedding = %s::vector "
                    "WHERE shop_id = %s AND LOWER(name) = LOWER(%s) AND embedding IS NULL",
                    (str(vec), shop_id, name),
                )
        conn.commit()
        log.info("Embeddings updated for %d products in shop %d", len(products), shop_id)
    except Exception:
        log.exception("Embedding update failed for shop %d", shop_id)


def import_products(products: list[dict], replace: bool = False, shop_id: int | None = None) -> int:
    ensure_app_tables()
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    conn = get_db()
    try:
        if replace:
            conn.execute(f"DELETE FROM orders WHERE shop_id = {ph}", (shop_id,))
            conn.execute(f"DELETE FROM products WHERE shop_id = {ph}", (shop_id,))

        for item in products:
            if not replace:
                _delete_existing(conn, shop_id, item)
            _insert_product(conn, shop_id, item)

        conn.commit()

        if USE_POSTGRES:
            _update_embeddings(conn, shop_id, products)

        return len(products)
    finally:
        conn.close()


def replace_products(products: list[dict]) -> int:
    return import_products(products, replace=True)


def update_product(
    product_id: int,
    price: int | None = None,
    quantity: int | None = None,
    shop_id: int | None = None,
) -> bool:
    if price is None and quantity is None:
        return False
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    sets = []
    params: list = []
    if price is not None:
        sets.append(f"price = {ph}")
        params.append(price)
    if quantity is not None:
        sets.append(f"quantity = {ph}")
        params.append(quantity)
    params.extend([product_id, shop_id])
    execute_write(
        f"UPDATE products SET {', '.join(sets)} WHERE id = {ph} AND shop_id = {ph}",
        params,
        fetch_one=False,
    )
    return True


def list_in_stock_categories(shop_id: int | None = None) -> list[str]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    rows = fetch_all(
        f"""
        SELECT DISTINCT category FROM products
        WHERE shop_id = {ph} AND quantity > 0 AND category IS NOT NULL AND category <> ''
        ORDER BY category
        """,
        (shop_id,),
    )
    return [row["category"] for row in rows]


def get_catalog_summary(shop_id: int | None = None, limit: int = 60) -> list[dict]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    return fetch_all(
        f"""
        SELECT name,
               category,
               MIN(price) AS min_price,
               MAX(price) AS max_price,
               SUM(quantity) AS total_qty,
               COUNT(*) AS variants
        FROM products
        WHERE shop_id = {ph} AND quantity > 0
        GROUP BY name, category
        ORDER BY name
        LIMIT {ph}
        """,
        (shop_id, limit),
    )


def _search_word_variants(word: str) -> list[str]:
    """Extra variants for common RU typos and plurals: мячь → мяч, мячи → мяч."""
    variants = [word]
    if len(word) > 3 and word.endswith("ь"):
        variants.append(word[:-1])
    if len(word) > 4 and word.endswith("ий"):
        variants.append(word[:-2] + "и")
    if len(word) > 3 and word.endswith("и"):
        variants.append(word[:-1])
    if len(word) > 3 and word.endswith("ы"):
        variants.append(word[:-1])
    out = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out


# Phonetic Cyrillic → Latin. Digraphs first so 'ш'→'sh' resolves before single
# letters. This is SCRIPT-level only — no brand or vertical words — so it stays
# universal: it just lets a customer's Cyrillic spelling of a Latin catalog token
# ('адидас') line up with the catalog's own word ('Adidas').
_TRANSLIT_PAIRS = [
    ("щ", "sch"), ("ш", "sh"), ("ч", "ch"), ("ц", "ts"), ("ю", "yu"),
    ("я", "ya"), ("ж", "zh"), ("х", "kh"), ("ё", "e"), ("й", "y"),
    ("ъ", ""), ("ь", ""),
    ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("д", "d"), ("е", "e"),
    ("з", "z"), ("и", "i"), ("к", "k"), ("л", "l"), ("м", "m"), ("н", "n"),
    ("о", "o"), ("п", "p"), ("р", "r"), ("с", "s"), ("т", "t"), ("у", "u"),
    ("ф", "f"), ("ы", "y"), ("э", "e"),
]


def _translit_cyr_to_lat(word: str) -> str:
    w = (word or "").lower()
    if not any("а" <= ch <= "я" or ch == "ё" for ch in w):
        return w
    for cyr, lat in _TRANSLIT_PAIRS:
        w = w.replace(cyr, lat)
    return w


def _catalog_name_tokens(products: list[dict]) -> list[str]:
    """Distinct lowercase word tokens (≥3 chars) from product names — the shop's
    OWN vocabulary, so any bridge can only ever match words that really exist."""
    tokens: set[str] = set()
    for p in products:
        for tok in re.findall(r"[a-zа-яё0-9]+", (p.get("name") or "").lower()):
            if len(tok) >= 3:
                tokens.add(tok)
    return list(tokens)


def _bridge_translit_to_catalog(
    words: list[str], catalog_tokens: list[str], cutoff: float = 0.72
) -> list[str]:
    """Bridge phonetic Cyrillic brand spellings to the catalog's Latin tokens.

    For each Cyrillic query word we transliterate ('баланс' → 'balans') and fuzzy-
    match it against the shop's own name tokens ('balance'). Returns the matched
    catalog tokens to add to the keyword search. No hardcoded brands: it only ever
    returns tokens already present in THIS shop's catalog, so it stays universal."""
    if not catalog_tokens:
        return []
    extra: list[str] = []
    for w in words:
        lat = _translit_cyr_to_lat(w)
        if lat == w or len(lat) < 3:
            continue  # not Cyrillic (or too short) — nothing to bridge
        for match in difflib.get_close_matches(lat, catalog_tokens, n=2, cutoff=cutoff):
            if match not in extra and match not in words:
                extra.append(match)
    return extra


def extract_query_words(query: str) -> list[str]:
    q_lower = query.lower()
    raw = [
        w for w in re.findall(r"[\w-]+", q_lower)
        if len(w) >= 2 and w not in STOP_WORDS
    ]
    out: list[str] = []
    for w in raw:
        for variant in _search_word_variants(w):
            if variant not in out:
                out.append(variant)
    return out


def search_products_db(
    words: list[str],
    shop_id: int,
    limit: int = 15,
    *,
    in_stock_only: bool = True,
) -> list[dict]:
    if not words:
        return []
    ph = db_placeholder()
    parts = []
    params: list = []
    for w in words:
        word_parts = []
        for variant in _search_word_variants(w):
            like = f"%{variant}%"
            if USE_POSTGRES:
                word_parts.append(
                    f"(LOWER(name) LIKE {ph} OR LOWER(COALESCE(description, '')) LIKE {ph} "
                    f"OR LOWER(COALESCE(sku, '')) LIKE {ph} OR LOWER(COALESCE(category, '')) LIKE {ph} "
                    f"OR LOWER(COALESCE(attributes::text, '')) LIKE {ph})"
                )
            else:
                word_parts.append(
                    f"(LOWER(name) LIKE {ph} OR LOWER(COALESCE(description, '')) LIKE {ph} "
                    f"OR LOWER(COALESCE(sku, '')) LIKE {ph} OR LOWER(COALESCE(category, '')) LIKE {ph} "
                    f"OR LOWER(COALESCE(attributes, '')) LIKE {ph})"
                )
            params.extend([like, like, like, like, like])
        parts.append(f"({' OR '.join(word_parts)})")

    stock_clause = " AND quantity > 0" if in_stock_only else ""
    rows = fetch_all(
        f"""
        SELECT id, name, description, sku, category, price, quantity, attributes
        FROM products
        WHERE shop_id = {ph}{stock_clause} AND ({' OR '.join(parts)})
        ORDER BY quantity DESC, name, sku
        LIMIT {ph}
        """,
        [shop_id, *params, limit],
    )
    return [_normalize_row(r) for r in rows]


def get_all_catalog_products(shop_id: int | None = None) -> list[dict]:
    """All in-stock products for AI-based relevance search."""
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    rows = fetch_all(
        f"""
        SELECT id, name, description, sku, category, price, quantity, attributes
        FROM products
        WHERE shop_id = {ph} AND quantity > 0
        ORDER BY name, sku, id
        """,
        (shop_id,),
    )
    return [_normalize_row(r) for r in rows]


def get_products_by_ids(shop_id: int | None, product_ids: list[int]) -> list[dict]:
    ids = [int(i) for i in product_ids if i is not None]
    if not ids:
        return []
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    placeholders = ", ".join([ph] * len(ids))
    rows = fetch_all(
        f"""
        SELECT id, name, description, sku, category, price, quantity, attributes
        FROM products
        WHERE shop_id = {ph} AND id IN ({placeholders})
        ORDER BY name, sku, id
        """,
        [shop_id, *ids],
    )
    return [_normalize_row(r) for r in rows]


def get_catalog_sample(shop_id: int | None = None, limit: int = 5) -> list[dict]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    rows = fetch_all(
        f"""
        SELECT id, name, description, sku, category, price, quantity, attributes
        FROM products
        WHERE shop_id = {ph} AND quantity > 0
        ORDER BY name
        LIMIT {ph}
        """,
        (shop_id, limit),
    )
    return [_normalize_row(r) for r in rows]


# Colour adjective roots (ё already folded to е). Matching is by ROOT so any
# inflection (синий/синие/синих) lines up with the stored colour, and a real
# adjective ENDING is required so a noun that merely starts with the root
# ('синтетика', 'бельё') can't be mistaken for a colour.
_COLOR_STEMS = (
    "бел", "черн", "красн", "син", "голуб", "зелен", "сер", "коричнев",
    "бежев", "желт", "оранжев", "розов", "фиолетов", "бордов", "сиренев",
)
_COLOR_ENDINGS = (
    "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
    "ого", "его", "ому", "ему", "ым", "им", "ом", "ем", "ых", "их", "ую", "юю",
)
_COLOR_RE = re.compile(
    r"\b(" + "|".join(_COLOR_STEMS) + r")(?:" + "|".join(_COLOR_ENDINGS) + r")\b"
)


def _norm_yo(text: str) -> str:
    """Fold ё→е so 'чёрный'/'черный' compare equal against a stored 'чёрный'."""
    return (text or "").replace("ё", "е")


def extract_attribute_filters(query: str) -> dict[str, str]:
    filters: dict[str, str] = {}
    q = _norm_yo(query.lower())
    size_match = re.search(r"\b(?:р(?:азмер)?\.?\s*)?([3-4][0-9](?:[.,]5)?)\b", q)
    if size_match:
        filters["size"] = size_match.group(1).replace(",", ".")
    color_match = _COLOR_RE.search(q)
    if color_match:
        filters["color"] = color_match.group(1)  # the root, e.g. 'син'
    return filters


def _content_words(query: str) -> list[str]:
    """Meaningful words (minus stopwords), WITHOUT morphological variants — used
    for the browse check, where variants like 'весь'→'вес' would only add noise."""
    return [
        w for w in re.findall(r"[\w-]+", (query or "").lower())
        if len(w) >= 2 and w not in STOP_WORDS
    ]


def is_browse_query(query: str) -> bool:
    content = _content_words(query)
    if not content:
        return True  # only greeting/stopwords/punctuation → show the catalog
    # Browse ONLY when EVERY meaningful word is browse vocabulary ('весь каталог',
    # 'покажи товары'). A topic word alongside it ('товары для ФУТБОЛА') means the
    # customer wants a search — the old substring test matched 'товары', dumped the
    # whole catalog and silently dropped 'футбол' (BUG 1).
    return all(w in BROWSE_VOCAB for w in content)


def _fmt_attrs(attrs: dict) -> str:
    if not attrs:
        return ""
    parts = [f"{k}:{v}" for k, v in attrs.items()]
    return " [" + ", ".join(parts) + "]"


def _fmt_summary_block(rows: list[dict], header: str = "") -> str:
    lines = [header] if header else []
    for r in rows:
        price = r.get("min_price") or r.get("price") or 0
        max_p = r.get("max_price")
        price_str = f"от {price}₸" if max_p and max_p != price else f"{price}₸"
        cat = r.get("category") or "—"
        qty = r.get("total_qty") or r.get("quantity") or 0
        variants = r.get("variants")
        variant_str = f"|{variants} вар." if variants and variants > 1 else ""
        lines.append(f"{r['name']}|{cat}|{price_str}|остаток {qty}{variant_str}")
    return "\n".join(lines)


def _fmt_products_block(items: list[dict]) -> str:
    lines = []
    for s in items:
        sku = f" SKU:{s['sku']}" if s.get("sku") else ""
        lines.append(
            f"  {s['name']}{sku}{_fmt_attrs(s.get('attributes') or {})}|{s['price']}₸|qty {s['quantity']}"
        )
    return "\n".join(lines)


def _trim_to_limit(text: str, limit: int = CATALOG_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    out = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > limit:
            out.append("... (показаны первые позиции)")
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


def _matches_attr_filters(item: dict, filters: dict[str, str]) -> bool:
    if not filters:
        return True
    attrs = item.get("attributes") or {}
    attrs_text = _norm_yo(json.dumps(attrs, ensure_ascii=False).lower())
    name_desc = _norm_yo(f"{item.get('name', '')} {item.get('description', '')}".lower())
    for key, val in filters.items():
        val_l = _norm_yo(val.lower())
        attr_val = _norm_yo(str(attrs.get(key, "")).lower())
        if val_l in attr_val or val_l in attrs_text or val_l in name_desc:
            continue
        return False
    return True


def _any_color_known(products: list[dict]) -> bool:
    """True if at least one product records a colour — so we never empty out a
    shop that doesn't track colour just because the customer named one."""
    return any((p.get("attributes") or {}).get("color") for p in products)


def _apply_attr_filters(query: str, products: list[dict]) -> list[dict]:
    """Enforce colour/size as HARD filters on top of the loose LLM/keyword search.

    Colour: enforced only when the catalog records colours; a genuine mismatch
    yields [] on purpose — 'синий' must return nothing rather than fall back to
    black. Size: only narrows when something matches, so a misparsed model-number
    can't wipe the results."""
    if not products:
        return products
    filters = extract_attribute_filters(query)
    color = filters.get("color")
    if color and _any_color_known(products):
        products = [p for p in products if _matches_attr_filters(p, {"color": color})]
    size = filters.get("size")
    if size and products:
        by_size = [p for p in products if _matches_attr_filters(p, {"size": size})]
        if by_size:
            products = by_size
    return products


def build_product_context(query: str, shop_id: int | None = None) -> tuple[str, int]:
    shop_id = resolve_shop_id(shop_id)
    categories = list_in_stock_categories(shop_id)
    cat_line = f"Категории на складе: {', '.join(categories)}" if categories else ""

    if is_browse_query(query):
        models = get_catalog_summary(shop_id, limit=60)
        if not models:
            return "Каталог пуст.", 0
        body = _fmt_summary_block(models, header=cat_line)
        return _trim_to_limit(body), len(models)

    words = extract_query_words(query)
    attr_filters = extract_attribute_filters(query)

    if not words and not attr_filters:
        models = get_catalog_summary(shop_id, limit=60)
        body = _fmt_summary_block(models, header=cat_line)
        return _trim_to_limit(body), len(models)

    matched = search_products_db(
        words, shop_id, limit=SKU_DETAIL_LIMIT * 2, in_stock_only=False,
    )
    if attr_filters:
        matched = [p for p in matched if _matches_attr_filters(p, attr_filters)]

    if not matched:
        return "", 0

    details = _fmt_products_block(matched[:SKU_DETAIL_LIMIT])
    ctx = f"{cat_line}\n\nПодходящие позиции:\n{details}" if cat_line else f"Подходящие позиции:\n{details}"
    return _trim_to_limit(ctx), len(matched[:SKU_DETAIL_LIMIT])


def search_products(query: str, shop_id: int | None = None) -> list[dict]:
    shop_id = resolve_shop_id(shop_id)
    words = extract_query_words(query)
    if not words:
        return []
    items = search_products_db(words, shop_id, limit=10, in_stock_only=False)
    if items:
        return items
    return search_products_db(words, shop_id, limit=10)


def search_sneakers(query: str, shop_id: int | None = None) -> list[dict]:
    """Backward-compatible alias."""
    return search_products(query, shop_id)


def vector_search_products(
    query: str,
    shop_id: int,
    limit: int = SKU_DETAIL_LIMIT,
) -> list[dict]:
    """Semantic search via pgvector cosine similarity. Returns [] if unavailable."""
    from embeddings import embed_text, is_available
    if not is_available():
        return []

    vec = embed_text(query)
    if vec is None:
        return []

    try:
        rows = fetch_all(
            """
            SELECT id, name, description, sku, category, price, quantity, attributes
            FROM products
            WHERE shop_id = %s AND quantity > 0 AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (shop_id, str(vec), limit),
        )
        results = [_normalize_row(r) for r in rows]
        log.info("Vector search shop=%s query=%r hits=%d", shop_id, query[:60], len(results))
        return results
    except Exception:
        log.exception("Vector search failed for shop %d", shop_id)
        return []


async def get_relevant_products(
    query: str,
    shop_id: int | None = None,
    *,
    shop: dict | None = None,
    api_key: str | None = None,
    limit: int = SKU_DETAIL_LIMIT,
    history: list[dict] | None = None,
) -> list[dict]:
    """Semantic product search: pgvector first, Groq AI fallback."""
    if is_browse_query(query):
        return []

    shop_id = resolve_shop_id(shop_id)

    # Fast semantic search — no tokens consumed
    found = vector_search_products(query, shop_id, limit=limit)
    if found:
        return _apply_attr_filters(query, found)

    # Deterministic keyword backstop — data-driven (matches the shop's OWN catalog
    # words + morphological variants, nothing hardcoded), so it works for any
    # vertical. Cross-language/slang recall is handled semantically by the LLM
    # search prompt. Runs regardless of api_key so search works without Groq.
    words = extract_query_words(query)
    all_products = get_all_catalog_products(shop_id)

    # Cross-script bridge: a phonetic Cyrillic brand ('адидас', 'нью баланс') won't
    # substring-match the catalog's Latin words. Transliterate + fuzzy-match the
    # query words against the shop's own name tokens and add the hits, so the
    # keyword search recovers them instead of dead-ending in 'не нашёл' (BUG 2).
    if words and all_products:
        bridged = _bridge_translit_to_catalog(words, _catalog_name_tokens(all_products))
        if bridged:
            words = words + bridged
            log.info("Translit bridge shop=%s added=%s", shop_id, bridged)

    kw = search_products_db(words, shop_id, limit=limit) if words else []

    llm: list[dict] = []
    if all_products and api_key:
        from ai import find_products_via_ai
        from shops import get_shop_by_id

        shop = shop or get_shop_by_id(shop_id) or {}
        llm = await find_products_via_ai(
            shop, query, all_products, api_key, shop_id, history=history or [],
        )

    # LLM (semantic) hits first, then keyword-only additions as a recall backstop.
    merged: list[dict] = list(llm)
    seen = {p.get("id") for p in merged}
    for p in kw:
        if p.get("id") not in seen:
            merged.append(p)
            seen.add(p.get("id"))

    # Colour/size are structured facts — enforce them deterministically so a loose
    # LLM match ('синий' → a black SKU coded '-B') can't leak the wrong colour.
    return _apply_attr_filters(query, merged)[:limit]


def _stock_label(qty: int) -> str:
    return "в наличии" if qty > 0 else "нет в наличии"


def _variant_size(item: dict) -> str:
    """Size value for a product row, if the catalog records one ('размер'/'size')."""
    attrs = item.get("attributes") or {}
    for key in ("размер", "size", "Размер", "Size"):
        val = attrs.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def _group_variants(items: list[dict]) -> list[dict]:
    """Collapse rows that differ only by size into one card per (name, price).

    The catalog stores each size as its own SKU, so a model with three sizes would
    otherwise print as three identical lines and read like a broken bot. Sizes are
    gathered so the customer still sees which exist. Display-only — callers keep the
    raw per-size rows for the order flow."""
    groups: list[dict] = []
    index: dict[tuple, dict] = {}
    for item in items:
        name = (item.get("name") or "—").strip()
        price = int(item.get("price") or 0)
        key = (name.lower(), price)
        g = index.get(key)
        if g is None:
            g = {"name": name, "price": price, "qty": 0, "sizes": []}
            index[key] = g
            groups.append(g)
        g["qty"] += int(item.get("quantity") or 0)
        size = _variant_size(item)
        if size and size not in g["sizes"]:
            g["sizes"].append(size)
    return groups


def _sizes_note(sizes: list[str]) -> str:
    if not sizes:
        return ""
    try:  # numeric sizes read better sorted (42, 43, 44) than in match order
        ordered = sorted(sizes, key=lambda s: float(str(s).replace(",", ".")))
    except ValueError:
        ordered = sizes
    label = "размер" if len(ordered) == 1 else "размеры"
    return f" ({label}: " + ", ".join(str(s) for s in ordered) + ")"


def format_catalog_reply(items: list[dict]) -> str:
    """Human reply built only from catalog rows — no LLM. Size variants that share a
    name+price are collapsed to ONE line, so the list never prints the same model
    several times over."""
    if not items:
        return "Сейчас не вижу такого товара на складе. Напишите название или категорию — проверю по каталогу."
    groups = _group_variants(items)

    if len(groups) == 1:
        g = groups[0]
        # A single physical row keeps its description; a collapsed multi-size group
        # shows the available sizes instead.
        if len(items) == 1:
            item = items[0]
            qty = int(item.get("quantity") or 0)
            desc = (item.get("description") or "").strip()
            extra = f" {desc}" if desc else ""
            return f"В каталоге: {item['name']}{extra} — {item['price']}₸, {_stock_label(qty)}."
        return (
            f"В каталоге: {g['name']}{_sizes_note(g['sizes'])} — "
            f"{g['price']}₸, {_stock_label(g['qty'])}."
        )

    lines = []
    for g in groups[:5]:
        lines.append(
            f"• {g['name']}{_sizes_note(g['sizes'])} — {g['price']}₸, {_stock_label(g['qty'])}"
        )
    suffix = "" if len(groups) <= 5 else f" (и ещё {len(groups) - 5})"
    return "По каталогу нашёл:\n" + "\n".join(lines) + suffix


def _distinctive_model_terms(query: str) -> list[str]:
    """Query words distinctive enough to name a specific MODEL — ≥4 chars, not a
    colour, not browse filler. 'форум' qualifies; 'чёрного', 'цвета', 'весь' don't."""
    q = _norm_yo((query or "").lower())
    terms: list[str] = []
    for w in _content_words(q):
        if len(w) < 4 or w in BROWSE_VOCAB:
            continue
        if _COLOR_RE.search(w):  # a colour word, not a model name
            continue
        if w not in terms:
            terms.append(w)
    return terms


def find_unavailable_model(query: str, shop_id: int, shown: list[dict]) -> str | None:
    """Name of a catalog model the customer clearly asked for that is OUT OF STOCK
    and NOT among the in-stock `shown` matches — so the bot can say 'X нет в наличии'
    instead of silently substituting a different model (e.g. asked for Adidas Forum,
    which is sold out, and got shown Ultraboost).

    Conservative by design: needs a distinctive query term (≥4 chars, not a colour)
    that (a) no shown product covers and (b) appears in the name of an out-of-stock
    catalog row. A false 'нет в наличии' is worse than staying silent."""
    terms = _distinctive_model_terms(query)
    if not terms or not shown:
        return None

    shown_tokens: set[str] = set()
    for p in shown:
        for tok in re.findall(r"[a-zа-яё0-9]+", _norm_yo((p.get("name") or "").lower())):
            if len(tok) >= 3:
                shown_tokens.add(tok)

    def _covers(term: str) -> bool:
        t_lat = _translit_cyr_to_lat(term)
        for tok in shown_tokens:
            if term in tok or tok in term:
                return True
            if t_lat and t_lat in _translit_cyr_to_lat(tok):
                return True
        return False

    absent = [t for t in terms if not _covers(t)]
    if not absent:
        return None

    candidates = search_products_db(
        extract_query_words(query), shop_id, limit=20, in_stock_only=False,
    )
    for p in candidates:
        if int(p.get("quantity") or 0) > 0:
            continue  # only out-of-stock rows are "unavailable"
        name_tokens = re.findall(r"[a-zа-яё0-9]+", _norm_yo((p.get("name") or "").lower()))
        for t in absent:
            t_lat = _translit_cyr_to_lat(t)
            if any(t in tok or tok in t or (t_lat and t_lat in _translit_cyr_to_lat(tok))
                   for tok in name_tokens):
                return (p.get("name") or "").strip() or None
    return None


def format_browse_reply(shop_id: int | None = None) -> str:
    """List in-stock catalog without LLM."""
    shop_id = resolve_shop_id(shop_id)
    models = get_catalog_summary(shop_id, limit=12)
    if not models:
        return "Каталог пока пуст. Уточните у менеджера, когда появятся товары."
    shown = models[:7]
    lines = [f"• {r['name']} — {r.get('min_price') or r.get('price') or 0}₸" for r in shown]
    body = "Вот что есть на складе:\n" + "\n".join(lines)
    if len(models) > 7:
        body += "\n…и не только — напишите категорию, чтобы сузить."
    return body + "\n\nЧто ищете? Назовите модель или задачу — подберу точнее 🙂"


async def get_relevant_sneakers(
    query: str,
    limit: int = SKU_DETAIL_LIMIT,
    shop_id: int | None = None,
    *,
    shop: dict | None = None,
    api_key: str | None = None,
) -> list[dict]:
    return await get_relevant_products(
        query, shop_id, shop=shop, api_key=api_key, limit=limit,
    )


def format_products_context(items: list[dict], shop_id: int | None = None) -> str:
    if not items:
        return "Нет совпадений в каталоге."
    return _fmt_products_block(items)


def format_sneakers_context(items: list[dict], shop_id: int | None = None) -> str:
    return format_products_context(items, shop_id)


# Legacy helpers used by public catalog API
def list_in_stock_brands(shop_id: int | None = None) -> list[str]:
    return list_in_stock_categories(shop_id)


def get_models_summary(shop_id: int | None = None, limit: int = 60) -> list[dict]:
    rows = get_catalog_summary(shop_id, limit=limit)
    for r in rows:
        r["brand"] = r.get("category") or ""
        r["model"] = r.get("name") or ""
    return rows
