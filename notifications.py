import asyncio
import logging

from shops import get_shop_by_id, get_shop_owner_email, get_shop_subscription_detail, resolve_shop_id

log = logging.getLogger(__name__)


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
