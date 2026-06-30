import asyncio
import logging
import time

from config import (
    DEGRADED_ALERT_COOLDOWN_SEC,
    DEGRADED_ALERT_MIN_SAMPLES,
    DEGRADED_ALERT_RATIO,
    DEGRADED_ALERT_WINDOW_SEC,
)
from shops import get_shop_by_id, get_shop_owner_email, get_shop_subscription_detail, resolve_shop_id

log = logging.getLogger(__name__)

_OWNER_ALERT_DEDUPE: dict[tuple[int, str], float] = {}
_OWNER_ALERT_WINDOW_SECONDS = 24 * 3600


def _claim_owner_alert(
    shop_id: int, kind: str, window_seconds: float = _OWNER_ALERT_WINDOW_SECONDS,
) -> bool:
    """True if this (shop_id, kind) hasn't been alerted within `window_seconds`
    (default 24h). Mutates state — records 'now' as the last-alert time on success."""
    key = (shop_id, kind)
    now = time.time()
    last = _OWNER_ALERT_DEDUPE.get(key, 0.0)
    if now - last < window_seconds:
        return False
    _OWNER_ALERT_DEDUPE[key] = now
    return True


def _owner_alert_active(shop_id: int, kind: str, window_seconds: float) -> bool:
    """Read-only: True if (shop_id, kind) was alerted within `window_seconds`. Used
    as a cheap pre-gate so we skip the DB query while a cooldown is in effect."""
    last = _OWNER_ALERT_DEDUPE.get((shop_id, kind), 0.0)
    return (time.time() - last) < window_seconds


def _order_message(
    shop_name: str,
    order_id: int | None,
    state: dict,
    channel: str,
    external_user_id: str,
) -> str:
    product = (state.get("product_interest") or "").strip() or "—"
    return (
        f"🛒 Новый заказ — {shop_name}\n"
        f"ID: {order_id or '—'}\n"
        f"Канал: {channel}\n"
        f"Клиент: {external_user_id}\n"
        f"Имя: {state.get('name', '')}\n"
        f"Телефон: {state.get('phone', '')}\n"
        f"Товар: {product}"
    )


async def _send_shop_telegram(shop: dict, text: str) -> bool:
    chat_id = (shop.get("owner_telegram_chat_id") or "").strip()
    token = (shop.get("tg_token") or "").strip()
    if not chat_id or not token:
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(chat_id), "text": text},
            )
            data = r.json()
            if data.get("ok"):
                return True
            log.error(
                "Shop Telegram notify failed shop=%s chat=%s: %s",
                shop.get("id"),
                chat_id,
                data.get("description", r.text),
            )
    except Exception as e:
        log.error("Shop Telegram notify failed shop=%s: %s", shop.get("id"), e)
    return False


async def email_shop_owner(shop_id: int, send_fn, *args, **kwargs) -> bool:
    """Send email to the address used at shop registration."""
    owner_email = get_shop_owner_email(shop_id)
    if not owner_email:
        log.warning("No owner_email for shop %s — email skipped", shop_id)
        return False
    if args:
        return await asyncio.to_thread(send_fn, args[0], owner_email, *args[1:], **kwargs)
    return await asyncio.to_thread(send_fn, owner_email, **kwargs)


async def notify_subscription_email(shop_id: int, *, reason: str = "updated") -> bool:
    shop = get_shop_by_id(shop_id)
    if not shop:
        return False
    sub = get_shop_subscription_detail(shop_id)
    from email_service import send_subscription_updated

    return await email_shop_owner(
        shop_id,
        send_subscription_updated,
        shop.get("name") or "Магазин",
        sub,
        reason=reason,
    )


async def notify_handoff(
    user_id: str,
    last_query: str,
    channel: str,
    external_user_id: str,
    shop_id: int | None = None,
    *,
    reason: str = "operator",
) -> None:
    """Notify shop owner that a client requested a live operator."""
    try:
        shop_id = resolve_shop_id(shop_id)
        shop = get_shop_by_id(shop_id)
        if not shop:
            return
        reason_text = "запросил оператора (/operator)" if reason == "operator" else "3 раза не нашёл товар"
        text = (
            f"🙋 Клиент ждёт оператора — {shop.get('name') or 'Магазин'}\n"
            f"Канал: {channel} | ID: {external_user_id}\n"
            f"Причина: {reason_text}\n"
            f"Последний запрос: {last_query[:200]}"
        )
        await _send_shop_telegram(shop, text)
    except Exception as e:
        log.error("Handoff notification failed: %s", e)


async def notify_owner_quota_exhausted(shop_id: int, used: int, limit: int) -> bool:
    """Alert shop owner via Telegram that the message quota is exhausted.

    Deduped to once per 24h per shop — the customer side switches to a neutral
    'manager will get back to you' reply, so the owner needs to know but should
    not be spammed with one alert per blocked message.
    """
    try:
        shop_id = resolve_shop_id(shop_id)
        if not _claim_owner_alert(shop_id, "quota"):
            return False
        shop = get_shop_by_id(shop_id)
        if not shop:
            return False
        text = (
            f"⚠️ Лимит сообщений исчерпан — {shop.get('name') or 'Магазин'}\n"
            f"Использовано: {used} из {limit}\n"
            "Сейчас клиенты получают нейтральный ответ вместо ответа бота. "
            "Продлите тариф в кабинете, чтобы бот снова отвечал."
        )
        return await _send_shop_telegram(shop, text)
    except Exception as e:
        log.error("Quota owner alert failed shop=%s: %s", shop_id, e)
        return False


async def notify_owner_subscription_expired(shop_id: int) -> bool:
    """Alert shop owner via Telegram that the subscription is inactive. 24h dedupe."""
    try:
        shop_id = resolve_shop_id(shop_id)
        if not _claim_owner_alert(shop_id, "subscription"):
            return False
        shop = get_shop_by_id(shop_id)
        if not shop:
            return False
        text = (
            f"⚠️ Подписка неактивна — {shop.get('name') or 'Магазин'}\n"
            "Сейчас клиенты получают нейтральный ответ вместо ответа бота. "
            "Откройте кабинет и активируйте тариф."
        )
        return await _send_shop_telegram(shop, text)
    except Exception as e:
        log.error("Subscription owner alert failed shop=%s: %s", shop_id, e)
        return False


async def maybe_alert_degradation_spike(shop_id: int, channel: str | None = None) -> bool:
    """Alert the shop owner when degraded_* replies spike — a sign Groq is storming.

    Called only from a degraded turn (see ai.save_ai_result). Cheap by design: the
    read-only cooldown gate short-circuits BEFORE any DB query, so during a storm we
    run the count query at most once per DEGRADED_ALERT_COOLDOWN_SEC. Fires one
    Telegram alert when the degraded share over the window crosses the ratio with
    enough samples. Never raises — alerting must not break the reply path."""
    try:
        shop_id = resolve_shop_id(shop_id)
        # Cooldown pre-gate (read-only): during a storm this is the common path and
        # avoids hitting the DB on every degraded reply.
        if _owner_alert_active(shop_id, "degradation", DEGRADED_ALERT_COOLDOWN_SEC):
            return False

        from conversations import degraded_reply_stats
        degraded, total = await asyncio.to_thread(
            degraded_reply_stats, shop_id, DEGRADED_ALERT_WINDOW_SEC
        )
        if total < DEGRADED_ALERT_MIN_SAMPLES:
            return False
        ratio = degraded / total
        if ratio < DEGRADED_ALERT_RATIO:
            return False

        # Claim the cooldown slot only now that we're actually alerting, so a
        # below-threshold check never consumes it.
        if not _claim_owner_alert(shop_id, "degradation", DEGRADED_ALERT_COOLDOWN_SEC):
            return False
        shop = get_shop_by_id(shop_id)
        if not shop:
            return False
        minutes = max(1, DEGRADED_ALERT_WINDOW_SEC // 60)
        pct = round(ratio * 100)
        text = (
            f"⚠️ Бот часто отвечает в упрощённом режиме — {shop.get('name') or 'Магазин'}\n"
            f"За последние ~{minutes} мин {pct}% ответов ({degraded} из {total}) — "
            "без ИИ, прямо по каталогу.\n"
            "Похоже, лимит Groq штормит. Проверьте тариф/нагрузку Groq."
        )
        log.warning(
            "Degradation spike shop=%s ratio=%.2f (%d/%d)", shop_id, ratio, degraded, total
        )
        return await _send_shop_telegram(shop, text)
    except Exception as e:
        log.error("Degradation spike alert failed shop=%s: %s", shop_id, e)
        return False


async def notify_shop_owner(
    order_id: int | None,
    state: dict,
    channel: str,
    external_user_id: str,
    shop_id: int | None = None,
) -> None:
    """Notify shop owner via Telegram + registration email."""
    try:
        shop_id = resolve_shop_id(shop_id)
        shop = get_shop_by_id(shop_id)
        if not shop:
            log.warning("Order notify: shop %s not found", shop_id)
            return

        text = _order_message(shop.get("name") or "Магазин", order_id, state, channel, external_user_id)

        tg_ok = await _send_shop_telegram(shop, text)
        if tg_ok:
            log.info("Order %s notified via Telegram for shop %s", order_id, shop_id)

        from email_service import send_new_order

        email_ok = await email_shop_owner(
            shop_id,
            send_new_order,
            shop.get("name") or "Магазин",
            order_id,
            state,
            channel,
            external_user_id,
        )
        if email_ok:
            log.info("Order %s emailed to owner (shop %s)", order_id, shop_id)

        if not tg_ok and not email_ok:
            log.warning("Order %s: no delivery channel for shop %s", order_id, shop_id)
    except Exception as e:
        log.error("Shop owner notification failed: %s", e)
