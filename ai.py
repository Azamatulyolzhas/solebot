import logging
import time

import httpx

from billing import (
    check_message_quota,
    is_subscription_active,
    quota_exceeded_message,
    resolve_groq_api_key,
)
from cache import (
    chat_sessions,
    check_rate_limit,
    save_session_message,
    set_last_product_interest,
)
from config import RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS
from conversations import (
    get_or_create_conversation,
    load_recent_messages,
    log_analytics_event,
    save_message,
    split_user_id,
)
from orders import handle_order_flow
from products import (
    format_browse_reply,
    format_catalog_reply,
    get_relevant_products,
    is_browse_query,
    search_products,
)
from shops import resolve_shop_id

log = logging.getLogger(__name__)


async def ask_ai(user_id: str, user_message: str, shop_id: int | None = None) -> str:
    if not user_message or not user_message.strip():
        return "Напишите, какой товар вас интересует — проверю наличие в каталоге."

    shop_id = resolve_shop_id(shop_id)
    if not is_subscription_active(shop_id):
        return "Подписка магазина истекла. Обратитесь к владельцу магазина."

    allowed, used, limit = check_message_quota(shop_id)
    if not allowed:
        return quota_exceeded_message(used, limit)

    if not resolve_groq_api_key(shop_id):
        return "ИИ-консультант временно недоступен. Добавьте Groq API ключ в настройках магазина."

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
    except Exception:
        log.exception("Conversation storage failed")
        await save_session_message(user_id, "user", user_message)

    order_reply = await handle_order_flow(user_id, user_message, shop_id)
    if order_reply:
        await save_ai_result(
            user_id, conversation_id, channel, user_message, order_reply,
            started_at, product_count, "order", shop_id=shop_id,
        )
        return order_reply

    try:
        matched = get_relevant_products(user_message, shop_id=shop_id)
        product_count = len(matched)
        log.info(
            "Catalog shop_id=%s hits=%s query=%r",
            shop_id, product_count, user_message[:100],
        )

        if matched:
            reply = format_catalog_reply(matched)
            interest = ", ".join(item["name"] for item in matched[:3])
            await set_last_product_interest(user_id, interest)
            mode = "catalog_exact"
        elif is_browse_query(user_message):
            reply = format_browse_reply(shop_id)
            mode = "catalog_browse"
        else:
            reply = fallback_reply(user_message, shop_id)
            mode = "catalog_fallback"
    except Exception:
        log.exception("Catalog reply failed")
        reply = fallback_reply(user_message, shop_id)
        mode = "catalog_fallback"

    await save_ai_result(
        user_id, conversation_id, channel, user_message, reply,
        started_at, product_count, mode, shop_id=shop_id,
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
    """Ответ из каталога без LLM — если поиск пустой."""
    try:
        items = search_products(user_message, shop_id)[:3]
        if items:
            return format_catalog_reply(items)
        from products import get_catalog_sample, format_products_context

        sample = get_catalog_sample(shop_id, limit=5)
        if sample:
            return (
                "Такого товара в каталоге нет. Вот что есть на складе: "
                + "; ".join(format_products_context(sample).splitlines())
            )
    except Exception:
        log.exception("Fallback search failed")
        return "Сейчас не получается проверить склад. Напишите название товара — менеджер уточнит наличие."

    return "Такого товара в каталоге нет. Напишите название или категорию — проверю по складу."
