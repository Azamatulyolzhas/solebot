import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

from admin_service import (
    count_logged_tokens,
    count_rows,
    count_shop_messages,
    count_shop_rows,
    require_admin,
)
from admin_ui import render_admin_page
from config import TELEGRAM_WEBHOOK_URL, USE_POSTGRES
from conversations import log_analytics_event
from products import import_products, list_products, products_to_csv, validate_product_csv
from orders import list_orders
from shops import get_default_shop_id, list_shops
from telegram_bot import shop_bots

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


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


@router.get("", response_class=HTMLResponse)
async def admin_page(request: Request):
    require_admin(request)
    return HTMLResponse(render_admin_page(request.query_params.get("token", "")))


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
