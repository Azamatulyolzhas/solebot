import csv
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

STOP_WORDS = {
    "есть", "ли", "какие", "какой", "какая", "какое", "хочу", "нужны", "нужен",
    "можно", "подскажи", "скажи", "покажи", "что", "это", "мне", "для", "вы",
    "нет", "как", "про", "по", "на", "из",
}

BROWSE_TERMS = {
    "каталог", "ассортимент", "товары", "товар", "что есть", "что у вас",
    "покажи все", "весь каталог", "catalog", "products",
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
        key, val = key.strip(), val.strip()
        if not key:
            continue
        try:
            if "." in val:
                attrs[key] = float(val.replace(",", "."))
            else:
                attrs[key] = int(val)
        except ValueError:
            attrs[key] = val
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

    return {
        "name": name,
        "description": (row.get("description") or "").strip() or None,
        "sku": (row.get("sku") or "").strip() or None,
        "category": (row.get("category") or "").strip() or None,
        "price": price,
        "quantity": quantity,
        "attributes": _parse_attributes_cell(row.get("attributes")),
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
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for item in products:
        attrs = item.get("attributes") or {}
        writer.writerow({
            "name": item.get("name", ""),
            "description": item.get("description") or "",
            "sku": item.get("sku") or "",
            "category": item.get("category") or "",
            "price": item.get("price", 0),
            "quantity": item.get("quantity", 0),
            "attributes": _attrs_json(attrs) if attrs else "",
        })
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
    """Extra variants for common RU typos: мячь → мяч."""
    variants = [word]
    if len(word) > 3 and word.endswith("ь"):
        variants.append(word[:-1])
    if len(word) > 4 and word.endswith("ий"):
        variants.append(word[:-2] + "и")
    out = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out


def extract_query_words(query: str) -> list[str]:
    q_lower = query.lower()
    return [
        w for w in re.findall(r"[\w-]+", q_lower)
        if len(w) >= 2 and w not in STOP_WORDS
    ]


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


def extract_attribute_filters(query: str) -> dict[str, str]:
    filters: dict[str, str] = {}
    q = query.lower()
    size_match = re.search(r"\b(?:р(?:азмер)?\.?\s*)?([3-4][0-9](?:[.,]5)?)\b", q)
    if size_match:
        filters["size"] = size_match.group(1).replace(",", ".")
    color_match = re.search(r"\b(бел\w+|черн\w+|красн\w+|син\w+|зелен\w+|сер\w+)\b", q)
    if color_match:
        filters["color"] = color_match.group(1)
    return filters


def is_browse_query(query: str) -> bool:
    q = query.lower()
    if any(term in q for term in BROWSE_TERMS):
        return True
    return not extract_query_words(query)


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
    attrs_text = json.dumps(attrs, ensure_ascii=False).lower()
    name_desc = f"{item.get('name', '')} {item.get('description', '')}".lower()
    for key, val in filters.items():
        val_l = val.lower()
        attr_val = str(attrs.get(key, attrs.get("size", ""))).lower()
        if val_l in attr_val or val_l in attrs_text or val_l in name_desc:
            continue
        if key == "size" and val_l in name_desc:
            continue
        return False
    return True


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


def get_relevant_products(query: str, limit: int = SKU_DETAIL_LIMIT, shop_id: int | None = None) -> list[dict]:
    shop_id = resolve_shop_id(shop_id)
    words = extract_query_words(query)
    if not words:
        return []
    items = search_products_db(words, shop_id, limit=limit, in_stock_only=False)
    if items:
        return items
    return search_products_db(words, shop_id, limit=limit)


def _stock_label(qty: int) -> str:
    return "в наличии" if qty > 0 else "нет в наличии"


def format_catalog_reply(items: list[dict]) -> str:
    """Human reply built only from catalog rows — no LLM."""
    if not items:
        return "Сейчас не вижу такого товара на складе. Напишите название или категорию — проверю по каталогу."
    if len(items) == 1:
        item = items[0]
        qty = int(item.get("quantity") or 0)
        desc = (item.get("description") or "").strip()
        extra = f" {desc}" if desc else ""
        return (
            f"В каталоге: {item['name']}{extra} — {item['price']}₸, {_stock_label(qty)}."
        )
    lines = []
    for item in items[:5]:
        qty = int(item.get("quantity") or 0)
        lines.append(f"• {item['name']} — {item['price']}₸, {_stock_label(qty)}")
    suffix = "" if len(items) <= 5 else f" (и ещё {len(items) - 5})"
    return "По каталогу нашёл:\n" + "\n".join(lines) + suffix


def format_browse_reply(shop_id: int | None = None) -> str:
    """List in-stock catalog without LLM."""
    shop_id = resolve_shop_id(shop_id)
    models = get_catalog_summary(shop_id, limit=30)
    if not models:
        return "Каталог пока пуст. Уточните у менеджера, когда появятся товары."
    lines = [f"• {r['name']} — {r.get('min_price') or r.get('price') or 0}₸" for r in models]
    return "Вот что есть на складе:\n" + "\n".join(lines)


def get_relevant_sneakers(query: str, limit: int = SKU_DETAIL_LIMIT, shop_id: int | None = None) -> list[dict]:
    return get_relevant_products(query, limit=limit, shop_id=shop_id)


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
