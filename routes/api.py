import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ai import ask_ai
from models import ChatRequest, ChatResponse
from config import TELEGRAM_BOT_TOKEN
from admin_service import get_database_status
from cache import get_redis_status
from products import get_catalog_summary, list_in_stock_categories, search_products_db
from shops import get_default_shop_id, resolve_shop_id

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/store", response_class=HTMLResponse)
async def store_page():
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "store" / "index.html"
    if not p.exists():
        return HTMLResponse("<h1>Store coming soon</h1>")
    return HTMLResponse(p.read_text(encoding="utf-8"))


@router.get("/api/catalog")
async def public_catalog(category: str = "", q: str = ""):
    """Публичный каталог для сайта клиентов."""
    shop_id = get_default_shop_id()
    rows = get_catalog_summary(shop_id, limit=200)
    categories = list_in_stock_categories(shop_id)

    if category:
        rows = [r for r in rows if (r.get("category") or "").lower() == category.lower()]
    if q:
        words = [w for w in q.lower().split() if len(w) > 2]
        if words:
            matched = search_products_db(words, resolve_shop_id(shop_id), limit=200)
            names = {m["name"] for m in matched}
            rows = [r for r in rows if r["name"] in names]

    return {
        "categories": categories,
        "count": len(rows),
        "items": rows,
    }


@router.get("/health")
async def health():
    from email_service import email_delivery_status

    db_status = get_database_status()
    redis_status = await get_redis_status()
    email_status = email_delivery_status()
    return {
        "status": "ok",
        **db_status,
        **redis_status,
        "email": {
            "configured": email_status.get("configured"),
            "production_ready": email_status.get("production_ready"),
            "from": email_status.get("from_address"),
        },
        "channels": {
            "telegram": bool(TELEGRAM_BOT_TOKEN),
            "web": True,
        },
    }


@router.post("/api/chat", response_model=ChatResponse)
async def web_chat(body: ChatRequest):
    user_id = f"web_{body.session_id}"
    reply = await ask_ai(user_id, body.message)
    return ChatResponse(reply=reply)


@router.post("/tg/webhook")
async def telegram_webhook(request: Request):
    from fastapi import HTTPException
    from telegram_bot import process_default_update, tg_bot

    if not tg_bot:
        raise HTTPException(503, "Telegram не настроен")
    try:
        data = await request.json()
        await process_default_update(data)
    except Exception as e:
        log.error(f"Telegram webhook processing failed: {e}")
        return {"ok": True, "ignored": True}
    return {"ok": True}


@router.post("/tg/{webhook_secret}/webhook")
async def telegram_shop_webhook(webhook_secret: str, request: Request):
    from fastapi import HTTPException
    from telegram_bot import process_shop_update

    try:
        data = await request.json()
        shop = await process_shop_update(webhook_secret, data)
    except KeyError:
        raise HTTPException(404, "Shop not found") from None
    except Exception as e:
        log.error(f"Telegram shop webhook processing failed [{webhook_secret}]: {e}")
        return {"ok": True, "ignored": True}
    return {"ok": True, "shop_id": shop.get("id")}


