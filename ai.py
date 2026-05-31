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
    build_product_context,
    search_sneakers,
)
from shops import resolve_shop_id

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты консультант магазина кроссовок. Отвечай по-русски, кратко (2-3 предложения).

КАТАЛОГ СКЛАДА (единственный источник правды):
{product_context}

СТРОГИЕ ПРАВИЛА:
1. Используй ТОЛЬКО модели из каталога выше — с их ценами, размерами и остатком.
2. НИКОГДА не выдумывай товары, бренды, цены или наличие из своих знаний.
3. В каталоге перечислены все модели магазина — не говори что есть только один бренд.
4. Цены указывай в ₸."""


async def ask_ai(user_id: str, user_message: str, shop_id: int | None = None) -> str:
    if not user_message or not user_message.strip():
        return "Напишите, какую модель, размер или стиль кроссовок вы ищете."

    shop_id = resolve_shop_id(shop_id)
    if not is_subscription_active(shop_id):
        return "Подписка магазина истекла. Обратитесь к владельцу магазина."

    started_at = time.perf_counter()
    channel, external_user_id = split_user_id(user_id)
    conversation_id = None
    product_count = 0

    allowed, _remaining = await check_rate_limit(user_id)
    if not allowed:
        reply = "Слишком много сообщений за минуту. Подождите немного и напишите снова."
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
        await save_ai_result(
            user_id, conversation_id, channel, user_message, order_reply,
            started_at, product_count, "order", shop_id=shop_id,
        )
        return order_reply

    try:
        product_context, product_count = build_product_context(user_message, shop_id=shop_id)
    except Exception:
        log.exception("RAG retrieval failed")
        product_context = ""
        product_count = 0

    log.info("RAG shop_id=%s hits=%s chars=%s query=%r",
             shop_id, product_count, len(product_context), user_message[:100])

    if not product_context:
        reply = fallback_reply(user_message, shop_id)
        await save_ai_result(
            user_id, conversation_id, channel, user_message, reply,
            started_at, product_count, "catalog_fallback", shop_id=shop_id,
        )
        return reply

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
                    "temperature": 0.3,
                    "messages": messages,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error(f"Groq request failed: {e}")
            reply = fallback_reply(user_message, shop_id)
            await save_ai_result(
                user_id, conversation_id, channel, user_message, reply,
                started_at, product_count, "fallback", shop_id=shop_id,
            )
            return reply

    data = resp.json()
    usage = data.get("usage") or {}

    if "error" in data:
        log.error(f"Groq error: {data['error']}")
        reply = fallback_reply(user_message, shop_id)
        await save_ai_result(
            user_id, conversation_id, channel, user_message, reply,
            started_at, product_count, "fallback", usage=usage, shop_id=shop_id,
        )
        return reply

    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "Ошибка, попробуйте позже.")

    await save_ai_result(
        user_id, conversation_id, channel, user_message, reply,
        started_at, product_count, "ai", usage=usage, shop_id=shop_id,
    )
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
    """Ответ из каталога без Groq — если поиск пустой или API недоступен."""
    try:
        items = search_sneakers(user_message, shop_id)[:3]
        if not items:
            from products import get_catalog_sample

            items = get_catalog_sample(shop_id, limit=5)
            if items:
                lines = []
                for item in items:
                    lines.append(
                        f"{item['brand']} {item['model']} {item.get('colorway') or ''}, "
                        f"размер {item['size']}, {item['price']}₸"
                    )
                return (
                    "Уточните бренд, модель или размер. А пока вот что есть на складе: "
                    + "; ".join(lines)
                )
    except Exception:
        log.exception("Fallback search failed")
        return "Сейчас не получается проверить склад. Напишите модель и размер — менеджер уточнит наличие."

    if not items:
        return "Сейчас не вижу точного совпадения на складе. Напишите бренд, модель или нужный размер — проверю по каталогу."

    lines = []
    for item in items:
        status = "есть" if item.get("quantity", 0) > 0 else "нет в наличии"
        lines.append(
            f"{item['brand']} {item['model']} {item.get('colorway') or ''}, "
            f"размер {item['size']}, {item['price']}₸ — {status}"
        )
    return "Нашёл по складу: " + "; ".join(lines)
