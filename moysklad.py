"""
МойСклад API client.
Docs: https://dev.moysklad.ru/doc/api/remap/1.2/
"""
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

MS_BASE = "https://api.moysklad.ru/api/remap/1.2"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }


async def get_stock(token: str) -> list[dict]:
    """
    Fetch current stock (остатки) for all products.
    Returns list of {name, article, quantity, price, ...}
    """
    url = f"{MS_BASE}/report/stock/all"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=_headers(token), params={"limit": 1000})
            resp.raise_for_status()
            data = resp.json()
            return data.get("rows", [])
    except Exception as e:
        log.error("МойСклад get_stock failed: %s", e)
        return []


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


def parse_stock_rows(rows: list[dict]) -> list[dict]:
    """
    Convert МойСклад stock rows into our internal format.
    Returns list of {name, article, quantity, price_rub}
    """
    result = []
    for r in rows:
        qty = r.get("quantity", 0)
        if qty < 0:
            qty = 0
        sale_price = r.get("salePrice", 0)
        if isinstance(sale_price, dict):
            price_kopecks = sale_price.get("value", 0) or 0
        else:
            price_kopecks = sale_price or 0
        result.append({
            "name":      r.get("name", ""),
            "article":   r.get("article") or r.get("code") or "",
            "quantity":  int(qty),
            "price_rub": int(price_kopecks) // 100,
        })
    return result
