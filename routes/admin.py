import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from admin_service import (
    count_logged_tokens,
    count_rows,
    count_shop_messages,
    count_shop_rows,
    list_recent_messages,
    require_admin,
)
from config import ADMIN_TOKEN, TELEGRAM_WEBHOOK_URL, USE_POSTGRES
from conversations import log_analytics_event
from orders import ORDER_STATUSES, list_orders, update_order_status
from products import import_products, list_products, products_to_csv, update_product, validate_product_csv
from shops import get_default_shop_id, list_pending_shops, list_shops, update_shop_status
from telegram_bot import shop_bots

log = logging.getLogger(__name__)

ADMIN_INDEX = Path(__file__).resolve().parent.parent / "admin" / "index.html"

router = APIRouter(prefix="/admin")


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ProductPatch(BaseModel):
    price: int | None = None
    quantity: int | None = None


class OrderPatch(BaseModel):
    status: str


# ── Routes ─────────────────────────────────────────────────────────────────────

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
async def admin_page():
    if not ADMIN_TOKEN:
        raise HTTPException(404, "Not found")
    return HTMLResponse(ADMIN_INDEX.read_text(encoding="utf-8"), headers=_NO_CACHE)


@router.get("/stats")
async def admin_stats(request: Request):
    require_admin(request)
    shop_id = get_default_shop_id()
    return {
        "database": "postgresql" if USE_POSTGRES else "sqlite",
        "shop_id": shop_id,
        "sneakers": count_shop_rows("sneakers", shop_id),
        "orders": count_shop_rows("orders", shop_id),
        "conversations": count_shop_rows("conversations", shop_id),
        "messages": count_shop_messages(shop_id),
        "analytics_events": count_shop_rows("analytics_events", shop_id),
        "total_tokens": count_logged_tokens(shop_id),
    }


@router.get("/messages")
async def admin_messages(request: Request, limit: int = 50):
    require_admin(request)
    shop_id = get_default_shop_id()
    limit = max(1, min(limit, 200))
    return {
        "shop_id": shop_id,
        "items": list_recent_messages(limit=limit, shop_id=shop_id),
    }


@router.get("/products")
async def admin_products(request: Request, limit: int = 100, offset: int = 0):
    require_admin(request)
    shop_id = get_default_shop_id()
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return {
        "shop_id": shop_id,
        "count": count_shop_rows("sneakers", shop_id),
        "limit": limit,
        "offset": offset,
        "items": list_products(limit=limit, offset=offset, shop_id=shop_id),
    }


@router.patch("/products/{product_id}")
async def admin_update_product(product_id: int, body: ProductPatch, request: Request):
    require_admin(request)
    if body.price is not None and body.price <= 0:
        raise HTTPException(400, "price must be > 0")
    if body.quantity is not None and body.quantity < 0:
        raise HTTPException(400, "quantity cannot be negative")
    shop_id = get_default_shop_id()
    update_product(product_id, price=body.price, quantity=body.quantity, shop_id=shop_id)
    return {"ok": True, "id": product_id}


@router.get("/orders")
async def admin_orders(request: Request, limit: int = 100, offset: int = 0):
    require_admin(request)
    shop_id = get_default_shop_id()
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return {
        "shop_id": shop_id,
        "count": count_shop_rows("orders", shop_id),
        "limit": limit,
        "offset": offset,
        "items": list_orders(limit=limit, offset=offset, shop_id=shop_id),
    }


@router.patch("/orders/{order_id}")
async def admin_update_order(order_id: int, body: OrderPatch, request: Request):
    require_admin(request)
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(ORDER_STATUSES)}")
    shop_id = get_default_shop_id()
    update_order_status(order_id, body.status, shop_id=shop_id)
    return {"ok": True, "id": order_id, "status": body.status}


@router.get("/applications")
async def admin_applications(request: Request):
    """List shops with status='pending' awaiting approval."""
    require_admin(request)
    return {"items": list_pending_shops()}


class ShopStatusPatch(BaseModel):
    status: str  # "active" | "rejected"


@router.patch("/shops/{shop_id}/status")
async def admin_update_shop_status(shop_id: int, body: ShopStatusPatch, request: Request):
    require_admin(request)
    allowed = ("active", "rejected", "suspended")
    if body.status not in allowed:
        raise HTTPException(400, f"status must be one of: {', '.join(allowed)}")
    update_shop_status(shop_id, body.status)
    return {"ok": True, "shop_id": shop_id, "status": body.status}


@router.get("/shops")
async def admin_shops(request: Request):
    require_admin(request)
    shops = list_shops()
    for shop in shops:
        secret = shop.get("tg_webhook_secret")
        shop["telegram_webhook_url"] = (
            TELEGRAM_WEBHOOK_URL.rstrip("/") + f"/tg/{secret}/webhook"
            if TELEGRAM_WEBHOOK_URL and secret
            else None
        )
    return {"shops": shops, "registered_bots": len(shop_bots)}


@router.get("/import-template")
async def admin_import_template(request: Request):
    require_admin(request)
    csv_text = (
        "brand,model,colorway,size,quantity,price,category,gender\n"
        "Nike,Air Force 1,White/White,42,7,45000,lifestyle,unisex\n"
        "Adidas,Samba OG,White/Black,43,4,52000,lifestyle,unisex\n"
    )
    return PlainTextResponse(csv_text, media_type="text/csv")


@router.get("/export")
async def admin_export(request: Request):
    require_admin(request)
    csv_text = products_to_csv(list_products(limit=10000, shop_id=get_default_shop_id()))
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=solebot-products.csv"},
    )


@router.post("/import-preview")
async def admin_import_preview(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")

    content = await file.read()
    result = validate_product_csv(content)
    preview = result["products"][:10]
    return {
        "filename": file.filename,
        "valid": result["valid"],
        "valid_rows": len(result["products"]),
        "error_count": len(result["errors"]),
        "errors": result["errors"][:50],
        "preview": preview,
        "next_step": "If valid is true, upload the same file to /admin/import to replace the catalog.",
    }


@router.post("/import")
async def admin_import(request: Request, file: UploadFile = File(...), replace: bool = False):
    require_admin(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")

    try:
        content = await file.read()
        result = validate_product_csv(content)
        if not result["valid"]:
            raise ValueError("; ".join(result["errors"][:10]))
        products = result["products"]
        imported = import_products(products, replace=replace, shop_id=get_default_shop_id())
        log_analytics_event(
            "admin",
            "products_imported",
            {"filename": file.filename, "imported": imported, "replace": replace},
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return {
        "status": "ok",
        "mode": "replace" if replace else "update",
        "imported": imported,
        "total_products": count_rows("sneakers"),
    }
