import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, EmailStr

from admin_service import (
    count_logged_tokens,
    count_rows,
    count_shop_messages,
    count_shop_rows,
    is_admin_configured,
    list_recent_messages,
    require_admin,
)
from auth import create_admin_token, verify_admin_credentials
from config import TELEGRAM_WEBHOOK_URL, USE_POSTGRES
from conversations import log_analytics_event
from orders import ORDER_STATUSES, list_orders, update_order_status
from products import import_products, list_products, products_to_csv, update_product, validate_product_csv
from email_service import send_shop_approved, send_shop_rejected
from notifications import notify_subscription_email
from shops import (
    ShopDeleteError,
    delete_shop,
    extend_shop_subscription,
    get_default_shop_id,
    get_shop_by_id,
    get_shop_subscription_detail,
    list_pending_shops,
    list_shops,
    update_shop_status,
)
from telegram_bot import shop_bots, unregister_shop_bot

log = logging.getLogger(__name__)

ADMIN_INDEX = Path(__file__).resolve().parent.parent / "admin" / "index.html"

router = APIRouter(prefix="/admin")


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ProductPatch(BaseModel):
    price: int | None = None
    quantity: int | None = None


class OrderPatch(BaseModel):
    status: str


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Routes ─────────────────────────────────────────────────────────────────────

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
async def admin_page():
    if not is_admin_configured():
        raise HTTPException(404, "Not found")
    return HTMLResponse(ADMIN_INDEX.read_text(encoding="utf-8"), headers=_NO_CACHE)


@router.post("/login")
async def admin_login(body: AdminLoginRequest):
    if not is_admin_configured():
        raise HTTPException(404, "Not found")
    if not verify_admin_credentials(body.email, body.password):
        raise HTTPException(401, "Неверный email или пароль")
    return {"token": create_admin_token(), "role": "admin"}


@router.get("/stats")
async def admin_stats(request: Request):
    require_admin(request)
    shop_id = get_default_shop_id()
    return {
        "database": "postgresql" if USE_POSTGRES else "sqlite",
        "shop_id": shop_id,
        "products": count_shop_rows("products", shop_id),
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
        "count": count_shop_rows("products", shop_id),
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
    allowed = ("active", "rejected", "suspended", "deleted")
    if body.status not in allowed:
        raise HTTPException(400, f"status must be one of: {', '.join(allowed)}")
    if body.status == "deleted":
        await unregister_shop_bot(shop_id)
    update_shop_status(shop_id, body.status)

    shop = get_shop_by_id(shop_id)
    if shop and shop.get("owner_email"):
        if body.status == "active":
            sub = get_shop_subscription_detail(shop_id)
            send_shop_approved(shop["name"], shop["owner_email"], sub)
        elif body.status == "rejected":
            send_shop_rejected(shop["name"], shop["owner_email"])

    return {"ok": True, "shop_id": shop_id, "status": body.status}


class SubscriptionPatch(BaseModel):
    plan: str        # "basic" | "pro"
    days: int = 30   # how many days to add


PLAN_LIMITS = {
    "trial":  500,
    "basic":  2000,
    "pro":    999999,
}


@router.patch("/shops/{shop_id}/subscription")
async def admin_extend_subscription(shop_id: int, body: SubscriptionPatch, request: Request):
    require_admin(request)
    allowed_plans = tuple(PLAN_LIMITS.keys())
    if body.plan not in allowed_plans:
        raise HTTPException(400, f"plan must be one of: {', '.join(allowed_plans)}")
    if not 1 <= body.days <= 365:
        raise HTTPException(400, "days must be between 1 and 365")
    messages_limit = PLAN_LIMITS[body.plan]
    ok = extend_shop_subscription(shop_id, body.plan, body.days, messages_limit)
    if not ok:
        raise HTTPException(500, "Failed to update subscription")
    await notify_subscription_email(shop_id, reason="updated")
    return {"ok": True, "shop_id": shop_id, "plan": body.plan, "days": body.days}


@router.delete("/shops/{shop_id}")
async def admin_delete_shop(
    shop_id: int,
    request: Request,
    hard: bool = False,
    confirm_slug: str = "",
):
    require_admin(request)
    shop = get_shop_by_id(shop_id)
    if not shop:
        raise HTTPException(404, "Магазин не найден")
    if hard:
        if not confirm_slug or confirm_slug.strip() != (shop.get("slug") or ""):
            raise HTTPException(400, "Для полного удаления укажите confirm_slug=slug магазина")
    try:
        await unregister_shop_bot(shop_id)
        delete_shop(shop_id, hard=hard)
    except ShopDeleteError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        log.exception("Delete shop failed: shop_id=%s hard=%s", shop_id, hard)
        detail = "Не удалось удалить магазин"
        if hard and "ForeignKeyViolation" in type(e).__name__:
            detail = "Не удалось удалить: остались связанные данные в БД. Попробуйте снова после обновления."
        raise HTTPException(500, detail) from e
    return {"ok": True, "shop_id": shop_id, "hard": hard}


@router.get("/shops")
async def admin_shops(request: Request, include_deleted: bool = False):
    require_admin(request)
    shops = list_shops(include_deleted=include_deleted)
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
        "total_products": count_rows("products"),
    }


class EmailDomainRequest(BaseModel):
    domain: str


class EmailTestRequest(BaseModel):
    to: EmailStr


@router.get("/email")
async def admin_email_status(request: Request):
    require_admin(request)
    from email_service import email_delivery_status

    return email_delivery_status()


@router.post("/email/domain")
async def admin_email_add_domain(body: EmailDomainRequest, request: Request):
    require_admin(request)
    from resend_email import create_domain

    try:
        data = create_domain(body.domain)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "domain": {
            "id": data.get("id"),
            "name": data.get("name"),
            "status": data.get("status"),
        },
        "records": data.get("records") or [],
    }


@router.post("/email/verify/{domain_id}")
async def admin_email_verify(domain_id: str, request: Request):
    require_admin(request)
    from resend_email import verify_domain

    try:
        data = verify_domain(domain_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **data}


@router.post("/email/test")
async def admin_email_test(body: EmailTestRequest, request: Request):
    require_admin(request)
    from resend_email import get_email_status, send_email

    status = get_email_status()
    if not status.get("configured"):
        raise HTTPException(400, "RESEND_API_KEY не настроен")
    ok, err = send_email(
        body.to,
        "Тест SaleBot",
        "<p>Если вы видите это письмо — email настроен правильно.</p>",
    )
    if not ok:
        raise HTTPException(502, err or "Не удалось отправить")
    return {"ok": True, "to": body.to, "from": status.get("from_address")}
