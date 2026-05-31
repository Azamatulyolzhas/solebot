import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ai import ask_ai
from models import ChatRequest, ChatResponse
from config import (
    INSTAGRAM_TOKEN,
    INSTAGRAM_VERIFY_TOKEN,
    TELEGRAM_BOT_TOKEN,
    WHATSAPP_TOKEN,
    WHATSAPP_VERIFY_TOKEN,
)
from admin_service import get_database_status
from cache import get_redis_status
from instagram_client import send_instagram
from whatsapp_client import send_whatsapp

log = logging.getLogger(__name__)

router = APIRouter()

WHATSAPP_VERIFY = WHATSAPP_VERIFY_TOKEN
INSTAGRAM_VERIFY = INSTAGRAM_VERIFY_TOKEN


@router.get("/")
async def health():
    db_status = get_database_status()
    redis_status = await get_redis_status()
    return {
        "status": "ok",
        **db_status,
        **redis_status,
        "channels": {
            "telegram": bool(TELEGRAM_BOT_TOKEN),
            "whatsapp": bool(WHATSAPP_TOKEN),
            "instagram": bool(INSTAGRAM_TOKEN),
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


@router.get("/wa/webhook")
async def whatsapp_verify(request: Request):
    from fastapi import HTTPException

    p = request.query_params
    if p.get("hub.verify_token") == WHATSAPP_VERIFY:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Неверный verify token")


@router.post("/wa/webhook")
async def whatsapp_message(request: Request):
    data = await request.json()
    try:
        entry = data["entry"][0]
        change = entry["changes"][0]["value"]
        msg = change["messages"][0]
        phone = msg["from"]
        text = msg["text"]["body"]
        user_id = f"wa_{phone}"

        reply = await ask_ai(user_id, text)
        await send_whatsapp(phone, reply)
    except (KeyError, IndexError):
        pass
    return {"status": "ok"}


@router.get("/ig/webhook")
async def instagram_verify(request: Request):
    from fastapi import HTTPException

    p = request.query_params
    if p.get("hub.verify_token") == INSTAGRAM_VERIFY:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Неверный verify token")


@router.post("/ig/webhook")
async def instagram_message(request: Request):
    data = await request.json()
    try:
        entry = data["entry"][0]
        msg = entry["messaging"][0]
        sender = msg["sender"]["id"]
        text = msg["message"]["text"]
        user_id = f"ig_{sender}"

        reply = await ask_ai(user_id, text)
        await send_instagram(sender, reply)
    except (KeyError, IndexError):
        pass
    return {"status": "ok"}
