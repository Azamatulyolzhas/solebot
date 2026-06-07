"""
МойСклад API client.
Docs: https://dev.moysklad.ru/doc/api/remap/1.2/
"""
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

MS_BASE = "https://api.moysklad.ru/api/remap/1.2"


def normalize_moysklad_token(token: str) -> str:
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {normalize_moysklad_token(token)}",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }


async def get_stock(token: str) -> tuple[list[dict], str | None]:
    """
    Fetch current stock (остатки) for all products.
    Returns (rows, error_message).
    """
    url = f"{MS_BASE}/report/stock/all"
    token = normalize_moysklad_token(token)
    if not token:
        return [], "Токен МойСклад пустой"

    all_rows: list[dict] = []
    offset = 0
    limit = 1000
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                resp = await client.get(
                    url,
                    headers=_headers(token),
                    params={"limit": limit, "offset": offset},
                )
                if resp.status_code >= 400:
                    body = resp.text[:300]
                    log.error("МойСклад get_stock HTTP %s: %s", resp.status_code, body)
                    return [], f"МойСклад API: HTTP {resp.status_code}. {body}"
                data = resp.json()
                rows = data.get("rows", [])
                all_rows.extend(rows)
                if len(rows) < limit:
                    break
                offset += limit
        return all_rows, None
    except Exception as e:
        log.error("МойСклад get_stock failed: %s", e)
        return [], f"Ошибка запроса к МойСклад: {e}"


async def get_product(token: str, product_href: str) -> Optional[dict]:
    """Fetch a single product by its href."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(product_href, headers=_headers(token))
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        log.error("МойСклад get_product failed: %s", e)
        return None


async def get_demand_positions(token: str, demand_href: str) -> list[dict]:
    """
    Fetch positions (line items) from a demand (отгрузка / retail demand).
    Returns list of {quantity, price, assortment_href, ...}
    """
    try:
        positions_href = demand_href.rstrip("/") + "/positions"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(positions_href, headers=_headers(token))
            resp.raise_for_status()
            return resp.json().get("rows", [])
    except Exception as e:
        log.error("МойСклад get_demand_positions failed: %s", e)
        return []


def rows_to_products(parsed: list[dict]) -> list[dict]:
    """Convert parsed МойСклад stock rows into catalog import format."""
    products = []
    for row in parsed:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        sku = (row.get("sku") or row.get("article") or "").strip() or None
        products.append({
            "name": name,
            "description": None,
            "sku": sku,
            "category": (row.get("category") or "").strip() or None,
            "price": max(int(row.get("price") or 0), 0),
            "quantity": max(int(row.get("quantity") or 0), 0),
            "attributes": row.get("attributes") or {},
        })
    return products


async def sync_moysklad_catalog(token: str, shop_id: int, replace: bool = False) -> dict:
    """Pull full stock from МойСклад and import into products table."""
    from products import import_products

    rows, error = await get_stock(token)
    if error:
        return {"imported": 0, "ms_rows": 0, "in_stock": 0, "error": error, "sample": []}
    if not rows:
        return {
            "imported": 0,
            "ms_rows": 0,
            "in_stock": 0,
            "error": "МойСклад вернул пустой список остатков",
            "sample": [],
        }

    parsed = parse_stock_rows(rows)
    products = rows_to_products(parsed)
    if not products:
        return {
            "imported": 0,
            "ms_rows": len(rows),
            "in_stock": 0,
            "error": "Не удалось разобрать товары из ответа МойСклад",
            "sample": [],
        }

    imported = import_products(products, replace=replace, shop_id=shop_id)
    in_stock = sum(1 for p in products if p["quantity"] > 0)
    sample = [p["name"] for p in products if p["quantity"] > 0][:8]
    log.info(
        "МойСклад catalog sync shop=%s replace=%s ms_rows=%s imported=%s in_stock=%s sample=%s",
        shop_id, replace, len(rows), imported, in_stock, sample,
    )
    return {
        "imported": imported,
        "ms_rows": len(rows),
        "in_stock": in_stock,
        "sample": sample,
        "shop_id": shop_id,
        "replace": replace,
    }


def parse_stock_rows(rows: list[dict]) -> list[dict]:
    """Convert МойСклад stock rows into our internal format."""
    result = []
    for r in rows:
        qty_raw = r.get("quantity")
        stock_raw = r.get("stock", 0)
        try:
            qty = int(float(qty_raw if qty_raw is not None else stock_raw))
        except (TypeError, ValueError):
            qty = 0
        try:
            stock = int(float(stock_raw or 0))
        except (TypeError, ValueError):
            stock = 0
        qty = max(qty, stock, 0)

        sale_price = r.get("salePrice", 0)
        if isinstance(sale_price, dict):
            price_kopecks = sale_price.get("value", 0) or 0
        else:
            try:
                price_kopecks = int(float(sale_price or 0))
            except (TypeError, ValueError):
                price_kopecks = 0
        if not price_kopecks:
            raw_price = r.get("price", 0)
            try:
                price_kopecks = int(float(raw_price or 0))
            except (TypeError, ValueError):
                price_kopecks = 0
        price = price_kopecks // 100

        article = r.get("article") or r.get("code") or ""
        folder = r.get("folder") or {}
        category = folder.get("pathName") or folder.get("name") or None

        result.append({
            "name": r.get("name", ""),
            "sku": article,
            "article": article,
            "quantity": qty,
            "price": price,
            "category": category,
        })
    return result
