import csv
import io
import re
import logging

from config import USE_POSTGRES
from db import db_placeholder, fetch_all, fetch_one_value, get_db
from schema import ensure_app_tables
from shops import resolve_shop_id

log = logging.getLogger(__name__)


def list_products(limit: int = 100, offset: int = 0, shop_id: int | None = None) -> list[dict]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    return fetch_all(
        f"""
        SELECT id, brand, model, colorway, size, quantity, price, category, gender
        FROM sneakers
        WHERE shop_id = {ph}
        ORDER BY brand, model, colorway, size
        LIMIT {ph} OFFSET {ph}
        """,
        (shop_id, limit, offset),
    )


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

def import_products(products: list[dict], replace: bool = False, shop_id: int | None = None) -> int:
    ensure_app_tables()
    shop_id = resolve_shop_id(shop_id)
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


def search_sneakers(query: str, shop_id: int | None = None) -> list[dict]:
    """РџРѕРёСЃРє РїРѕ СЃРєР»Р°РґСѓ вЂ” РїРѕ Р±СЂРµРЅРґСѓ, РјРѕРґРµР»Рё, СЂР°СЃС†РІРµС‚РєРµ, РєР°С‚РµРіРѕСЂРёРё"""
    words = [w for w in query.lower().split() if len(w) > 2]
    if not words:
        return []
    
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    conditions = " OR ".join(
        [f"(LOWER(brand) LIKE {ph} OR LOWER(model) LIKE {ph} OR LOWER(colorway) LIKE {ph} OR LOWER(category) LIKE {ph})"
         for _ in words]
    )
    params = []
    for w in words:
        params.extend([f"%{w}%"] * 4)
    
    return fetch_all(
        f"SELECT * FROM sneakers WHERE shop_id = {ph} AND ({conditions}) ORDER BY brand, model, size",
        [shop_id, *params],
    )

def extract_requested_size(query: str) -> float | None:
    match = re.search(r"\b(?:СЂ(?:Р°Р·РјРµСЂ)?\.?\s*)?([3-4][0-9](?:[.,]5)?)\b", query.lower())
    if not match:
        return None
    return float(match.group(1).replace(",", "."))

def get_relevant_sneakers(query: str, limit: int = 5, shop_id: int | None = None) -> list[dict]:
    """RAG retrieval: РґРѕСЃС‚Р°С‘Рј С‚РѕР»СЊРєРѕ СЃР°РјС‹Рµ РїРѕС…РѕР¶РёРµ С‚РѕРІР°СЂС‹ РґР»СЏ РїСЂРѕРјРїС‚Р°."""
    words = [w for w in re.findall(r"[\w-]+", query.lower()) if len(w) > 2]
    requested_size = extract_requested_size(query)
    if not words and requested_size is None:
        return []

    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
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
    params = [*score_params, shop_id, *where_params]
    params.append(limit)

    return fetch_all(
        f"""
        SELECT *, ({score_sql}) AS relevance
        FROM sneakers
        WHERE shop_id = {ph} AND ({where_sql}) AND quantity > 0
        ORDER BY relevance DESC, brand, model, size
        LIMIT {ph}
        """,
        params,
    )

def format_sneakers_context(items: list[dict]) -> str:
    if not items:
        return "РќРµС‚ С‚РѕС‡РЅС‹С… СЃРѕРІРїР°РґРµРЅРёР№ РІ РЅР°Р»РёС‡РёРё. РџРѕРїСЂРѕСЃРё СѓС‚РѕС‡РЅРёС‚СЊ Р±СЂРµРЅРґ, РјРѕРґРµР»СЊ, СЂР°Р·РјРµСЂ РёР»Рё СЃС‚РёР»СЊ."

    lines = []
    for item in items:
        colorway = item.get("colorway") or ""
        category = item.get("category") or ""
        lines.append(
            f"{item['brand']} {item['model']} {colorway}|"
            f"СЂР°Р·РјРµСЂ {item['size']}|{item['price']}в‚ё|"
            f"РѕСЃС‚Р°С‚РѕРє {item['quantity']}|{category}"
        )
    return "\n".join(lines)

def check_availability(brand: str = "", model: str = "", size: float = None) -> list[dict]:
    """РўРѕС‡РЅР°СЏ РїСЂРѕРІРµСЂРєР° РЅР°Р»РёС‡РёСЏ"""
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
    РћРџРўРРњРР—РђР¦РРЇ 1: РљРѕРјРїР°РєС‚РЅС‹Р№ С‚РµРєСЃС‚РѕРІС‹Р№ С„РѕСЂРјР°С‚ РІРјРµСЃС‚Рѕ JSON.
    Р­РєРѕРЅРѕРјРёСЏ ~61% С‚РѕРєРµРЅРѕРІ РЅР° РѕРїРёСЃР°РЅРёРё СЃРєР»Р°РґР°.
    Р¤РѕСЂРјР°С‚: Р‘СЂРµРЅРґ РњРѕРґРµР»СЊ | С†РµРЅР° | СЂР°Р·РјРµСЂС‹ | РЅР°Р»РёС‡РёРµ
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
        stock = "РµСЃС‚СЊ" if r["total_qty"] > 0 else "РЅРµС‚"
        lines.append(f"{r['brand']} {r['model']}|{r['price']}в‚ё|СЂ.{r['sizes']}|{stock}")
    return "\n".join(lines)
