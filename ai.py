import logging
import time

import httpx

from billing import is_subscription_active
from cache import (
    chat_sessions,
    check_rate_limit,
    load_session_history,
    save_session_message,
)
from config import GROQ_API_KEY, RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS
from conversations import (
    get_or_create_conversation,
    load_recent_messages,
    log_analytics_event,
    save_message,
    split_user_id,
)
from orders import handle_order_flow
from products import (
    format_sneakers_context,
    get_relevant_sneakers,
    search_sneakers,
)
from shops import resolve_shop_id

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """РљРѕРЅСЃСѓР»СЊС‚Р°РЅС‚ РјР°РіР°Р·РёРЅР° РєСЂРѕСЃСЃРѕРІРѕРє. РћС‚РІРµС‡Р°Р№ РїРѕ-СЂСѓСЃСЃРєРё, 2-3 РїСЂРµРґР»РѕР¶РµРЅРёСЏ РјР°РєСЃРёРјСѓРј.

РќРђР™Р”Р•РќРћ РќРђ РЎРљР›РђР”Р• (Р±СЂРµРЅРґ РјРѕРґРµР»СЊ С†РІРµС‚|СЂР°Р·РјРµСЂ|С†РµРЅР°|РѕСЃС‚Р°С‚РѕРє|РєР°С‚РµРіРѕСЂРёСЏ):
{product_context}

РџСЂР°РІРёР»Р°: РёСЃРїРѕР»СЊР·СѓР№ С‚РѕР»СЊРєРѕ РЅР°Р№РґРµРЅРЅС‹Рµ С‚РѕРІР°СЂС‹; С†РµРЅС‹ РІ в‚ё; РµСЃР»Рё С‚РѕС‡РЅРѕРіРѕ СЃРѕРІРїР°РґРµРЅРёСЏ РЅРµС‚ вЂ” Р·Р°РґР°Р№ СѓС‚РѕС‡РЅСЏСЋС‰РёР№ РІРѕРїСЂРѕСЃ РёР»Рё РїСЂРµРґР»РѕР¶Рё Р±Р»РёР¶Р°Р№С€РёРµ РІР°СЂРёР°РЅС‚С‹; РЅРµ РІС‹РґСѓРјС‹РІР°Р№."""


async def ask_ai(user_id: str, user_message: str, shop_id: int | None = None) -> str:
    """
    Groq API вЂ” llama-3.1-8b-instant.
    Р¤РѕСЂРјР°С‚ СЃРѕРІРјРµСЃС‚РёРј СЃ OpenAI: system РёРґС‘С‚ РїРµСЂРІС‹Рј СЃРѕРѕР±С‰РµРЅРёРµРј РІ messages[].
    РСЃС‚РѕСЂРёСЏ: РїРѕСЃР»РµРґРЅРёРµ 6 СЃРѕРѕР±С‰РµРЅРёР№ (3 РїР°СЂС‹) вЂ” СЌРєРѕРЅРѕРјРёСЏ С‚РѕРєРµРЅРѕРІ.
    """
    if not user_message or not user_message.strip():
        return "РќР°РїРёС€РёС‚Рµ, РєР°РєСѓСЋ РјРѕРґРµР»СЊ, СЂР°Р·РјРµСЂ РёР»Рё СЃС‚РёР»СЊ РєСЂРѕСЃСЃРѕРІРѕРє РІС‹ РёС‰РµС‚Рµ."

    shop_id = resolve_shop_id(shop_id)
    if not is_subscription_active(shop_id):
        return "РџРѕРґРїРёСЃРєР° РјР°РіР°Р·РёРЅР° РёСЃС‚РµРєР»Р°. РћР±СЂР°С‚РёС‚РµСЃСЊ Рє РІР»Р°РґРµР»СЊС†Сѓ РјР°РіР°Р·РёРЅР°."

    started_at = time.perf_counter()
    channel, external_user_id = split_user_id(user_id)
    conversation_id = None
    product_count = 0

    allowed, remaining = await check_rate_limit(user_id)
    if not allowed:
        reply = "РЎР»РёС€РєРѕРј РјРЅРѕРіРѕ СЃРѕРѕР±С‰РµРЅРёР№ Р·Р° РјРёРЅСѓС‚Сѓ. РџРѕРґРѕР¶РґРёС‚Рµ РЅРµРјРЅРѕРіРѕ Рё РЅР°РїРёС€РёС‚Рµ СЃРЅРѕРІР°."
        try:
            log_analytics_event(
                channel,
                "rate_limited",
                {
                    "user_message": user_message,
                    "limit": RATE_LIMIT_MESSAGES,
                    "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                },
                shop_id,
            )
        except Exception:
            log.exception("Rate limit analytics failed")
        return reply

    try:
        conversation_id = get_or_create_conversation(channel, external_user_id, shop_id)
        save_message(conversation_id, "user", user_message)
        await save_session_message(user_id, "user", user_message)
        history = await load_session_history(user_id)
        if not history:
            history = load_recent_messages(conversation_id, limit=6)
    except Exception:
        log.exception("Conversation storage failed")
        await save_session_message(user_id, "user", user_message)
        history = await load_session_history(user_id)

    order_reply = await handle_order_flow(user_id, user_message, shop_id)
    if order_reply:
        await save_ai_result(user_id, conversation_id, channel, user_message, order_reply, started_at, product_count, "order", shop_id=shop_id)
        return order_reply

    try:
        relevant_items = get_relevant_sneakers(user_message, limit=5, shop_id=shop_id)
        product_count = len(relevant_items)
        product_context = format_sneakers_context(relevant_items)
    except Exception:
        log.exception("RAG retrieval failed")
        product_context = "РЎРєР»Р°Рґ РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРµРЅ. РџРѕРїСЂРѕСЃРё РєР»РёРµРЅС‚Р° СѓС‚РѕС‡РЅРёС‚СЊ Р·Р°РїСЂРѕСЃ РёР»Рё РїРѕРґРѕР¶РґР°С‚СЊ РЅРµРјРЅРѕРіРѕ."

    # Groq: system РїРµСЂРµРґР°С‘С‚СЃСЏ РІРЅСѓС‚СЂРё messages РєР°Рє РїРµСЂРІС‹Р№ СЌР»РµРјРµРЅС‚
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(product_context=product_context)},
        *history,
    ]

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 250,
                    "temperature": 0.4,  # РЅРёР¶Рµ РґРµС„РѕР»С‚Р° вЂ” РјРµРЅСЊС€Рµ "С„Р°РЅС‚Р°Р·РёР№" РїСЂРѕ С‚РѕРІР°СЂС‹
                    "messages": messages,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error(f"Groq request failed: {e}")
            reply = fallback_reply(user_message, shop_id)
            await save_ai_result(user_id, conversation_id, channel, user_message, reply, started_at, product_count, "fallback", shop_id=shop_id)
            return reply

    data = resp.json()
    usage = data.get("usage") or {}

    if "error" in data:
        log.error(f"Groq error: {data['error']}")
        reply = fallback_reply(user_message, shop_id)
        await save_ai_result(user_id, conversation_id, channel, user_message, reply, started_at, product_count, "fallback", usage=usage, shop_id=shop_id)
        return reply

    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "РћС€РёР±РєР°, РїРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ.")

    await save_ai_result(user_id, conversation_id, channel, user_message, reply, started_at, product_count, "ai", usage=usage, shop_id=shop_id)
    return reply

async def save_ai_result(
    user_id: str,
    conversation_id: int | None,
    channel: str,
    user_message: str,
    reply: str,
    started_at: float,
    product_count: int,
    mode: str,
    usage: dict | None = None,
    shop_id: int | None = None,
) -> None:
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    usage = usage or {}
    try:
        if conversation_id is not None:
            save_message(conversation_id, "assistant", reply)
        else:
            chat_sessions.setdefault(user_id, []).append({"role": "assistant", "content": reply})
        await save_session_message(user_id, "assistant", reply)

        log_analytics_event(
            channel,
            "chat_reply",
            {
                "mode": mode,
                "user_message": user_message,
                "reply": reply,
                "latency_ms": latency_ms,
                "rag_products": product_count,
                "total_tokens": usage.get("total_tokens", 0),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            shop_id,
        )
    except Exception:
        log.exception("Saving AI result failed")

def fallback_reply(user_message: str, shop_id: int | None = None) -> str:
    """РџСЂРѕСЃС‚РѕР№ SQL-РѕС‚РІРµС‚, РµСЃР»Рё РР РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРµРЅ."""
    try:
        items = search_sneakers(user_message, shop_id)[:3]
    except Exception:
        log.exception("Fallback search failed")
        return "РЎРµР№С‡Р°СЃ РЅРµ РїРѕР»СѓС‡Р°РµС‚СЃСЏ РїСЂРѕРІРµСЂРёС‚СЊ СЃРєР»Р°Рґ. РќР°РїРёС€РёС‚Рµ, РїРѕР¶Р°Р»СѓР№СЃС‚Р°, РјРѕРґРµР»СЊ Рё СЂР°Р·РјРµСЂ вЂ” РјРµРЅРµРґР¶РµСЂ СѓС‚РѕС‡РЅРёС‚ РЅР°Р»РёС‡РёРµ."

    if not items:
        return "РЎРµР№С‡Р°СЃ РЅРµ РІРёР¶Сѓ С‚РѕС‡РЅРѕРіРѕ СЃРѕРІРїР°РґРµРЅРёСЏ РЅР° СЃРєР»Р°РґРµ. РќР°РїРёС€РёС‚Рµ Р±СЂРµРЅРґ, РјРѕРґРµР»СЊ РёР»Рё РЅСѓР¶РЅС‹Р№ СЂР°Р·РјРµСЂ вЂ” РїСЂРѕРІРµСЂСЋ РїРѕ РєР°С‚Р°Р»РѕРіСѓ."

    lines = []
    for item in items:
        status = "РµСЃС‚СЊ" if item.get("quantity", 0) > 0 else "РЅРµС‚ РІ РЅР°Р»РёС‡РёРё"
        lines.append(
            f"{item['brand']} {item['model']} {item.get('colorway') or ''}, "
            f"СЂР°Р·РјРµСЂ {item['size']}, {item['price']}в‚ё вЂ” {status}"
        )
    return "РќР°С€С‘Р» РїРѕ СЃРєР»Р°РґСѓ: " + "; ".join(lines)
