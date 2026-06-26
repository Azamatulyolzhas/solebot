import logging
import re

from cache import (
    clear_order_state,
    clear_product_context,
    get_order_state,
    set_order_state,
)
from config import USE_POSTGRES
from conversations import split_user_id
from db import db_placeholder, execute_write, fetch_all, fetch_one_value
from notifications import notify_shop_owner
from shops import resolve_shop_id

log = logging.getLogger(__name__)

ORDER_TRIGGERS = (
    "хочу купить",
    "оформить заказ",
    "хочу заказать",
)

GENERIC_ORDER_PHRASES = {
    "хочу купить",
    "оформить заказ",
    "хочу заказать",
}


def create_order(
    channel: str,
    external_user_id: str,
    customer_name: str,
    customer_phone: str,
    product_interest: str,
    shop_id: int | None = None,
) -> int | None:
    try:
        shop_id = resolve_shop_id(shop_id)
        ph = db_placeholder()
        # Funnel: snapshot whether this shop had any prior order BEFORE insert.
        is_first = (fetch_one_value(
            f"SELECT COUNT(*) FROM orders WHERE shop_id = {ph}", (shop_id,)
        ) or 0) == 0
        row = execute_write(
            f"""
            INSERT INTO orders
                (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, status)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
            """,
            (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, "new"),
            fetch_one=True,
        ) if USE_POSTGRES else None
        if USE_POSTGRES:
            new_id = row["id"] if row else None
        else:
            execute_write(
                f"""
                INSERT INTO orders
                    (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, status)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                """,
                (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, "new"),
            )
            new_id = fetch_one_value("SELECT MAX(id) FROM orders")

        if new_id and is_first:
            try:
                from conversations import log_analytics_event
                log_analytics_event(
                    channel, "first_lead",
                    {"order_id": new_id, "product": product_interest},
                    shop_id=shop_id,
                )
            except Exception:
                log.exception("first_lead funnel event failed for shop %s", shop_id)
        return new_id
    except Exception as e:
        log.error(f"Create order failed: {e}")
        return None


def list_orders(limit: int = 100, offset: int = 0, shop_id: int | None = None) -> list[dict]:
    ph = db_placeholder()
    try:
        shop_id = resolve_shop_id(shop_id)
        return fetch_all(
            f"""
            SELECT id, channel, external_user_id, customer_name, customer_phone,
                   product_interest, status, created_at
            FROM orders
            WHERE shop_id = {ph}
            ORDER BY id DESC
            LIMIT {ph} OFFSET {ph}
            """,
            (shop_id, limit, offset),
        )
    except Exception as e:
        log.error(f"List orders failed: {e}")
        return []


ORDER_STATUSES = ("new", "confirmed", "done", "cancelled")


def update_order_status(order_id: int, status: str, shop_id: int | None = None) -> bool:
    """Сменить статус заказа. Возвращает True если строка обновлена."""
    if status not in ORDER_STATUSES:
        return False
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    execute_write(
        f"UPDATE orders SET status = {ph} WHERE id = {ph} AND shop_id = {ph}",
        (status, order_id, shop_id),
    )
    return True


def looks_like_order_request(message: str) -> bool:
    text = message.lower().strip()
    return any(trigger in text for trigger in ORDER_TRIGGERS)


def looks_like_phone(message: str) -> bool:
    digits = re.sub(r"\D", "", message)
    return 10 <= len(digits) <= 15


def _normalize_product_interest(message: str) -> str:
    text = message.strip()
    lowered = text.lower().rstrip(".!")
    if lowered in GENERIC_ORDER_PHRASES:
        return ""
    for phrase in GENERIC_ORDER_PHRASES:
        if lowered == phrase or lowered.startswith(phrase + " "):
            rest = text[len(phrase):].strip(" .,!-—")
            if rest and rest.lower() not in GENERIC_ORDER_PHRASES:
                return rest
            return ""
    return text


async def _resolve_product_interest(user_id: str, user_message: str, shop_id: int | None = None) -> str:
    explicit = _normalize_product_interest(user_message)
    if explicit:
        return explicit
    # Pin the SINGLE product the customer confirmed in THIS session. On any
    # uncertainty return "" so the caller asks "which one?" instead of guessing.
    # We deliberately do NOT fall back to the running interest: that shipped the
    # whole discussed set — and stale cross-session leftovers — into the order
    # (the phantom-product bug, BUG 3).
    try:
        from ai import resolve_selected_product
        selected = await resolve_selected_product(user_id, resolve_shop_id(shop_id))
        if selected:
            return selected
    except Exception:
        log.exception("Selected-product resolution failed for %s", user_id)
    return ""


async def _resolve_confirmed_product(user_id: str, user_message: str, shop_id: int | None = None) -> str:
    """Resolve the product the customer named at the confirm step to a catalog
    label. Falls back to their literal text if search finds nothing — never to
    stale interest. Keeps the order tied to what the customer actually said."""
    text = user_message.strip()
    try:
        from ai import _product_label
        from products import get_relevant_products
        hits = await get_relevant_products(text, shop_id=resolve_shop_id(shop_id))
        if hits:
            return _product_label(hits[0])
    except Exception:
        log.exception("Confirm-product resolution failed for %s", user_id)
    return text


async def handle_order_flow(user_id: str, user_message: str, shop_id: int | None = None) -> str | None:
    try:
        channel, external_user_id = split_user_id(user_id)
        state = await get_order_state(user_id)

        if state is None:
            if not looks_like_order_request(user_message):
                return None

            product_interest = await _resolve_product_interest(user_id, user_message, shop_id)
            if not product_interest:
                # Couldn't pin a single confirmed product — ask instead of
                # guessing (a wrong binding order is worse than one question).
                await set_order_state(user_id, {"step": "confirm_product"})
                return "Подскажите, какой именно товар оформляем? Напишите название или модель."

            await set_order_state(user_id, {
                "step": "name",
                "product_interest": product_interest,
            })
            return "Отлично, оформим заказ. Напишите, пожалуйста, ваше имя."

        step = state.get("step")
        if step == "confirm_product":
            product_interest = await _resolve_confirmed_product(user_id, user_message, shop_id)
            state["product_interest"] = product_interest
            state["step"] = "name"
            await set_order_state(user_id, state)
            return "Отлично, оформим заказ. Напишите, пожалуйста, ваше имя."

        if step == "name":
            name = user_message.strip()
            if len(name) < 2:
                return "Напишите, пожалуйста, имя чуть подробнее."

            state["name"] = name
            state["step"] = "phone"
            await set_order_state(user_id, state)
            return "Спасибо. Теперь отправьте номер телефона для связи."

        if step == "phone":
            phone = user_message.strip()
            if not looks_like_phone(phone):
                return "Похоже, это не номер телефона. Отправьте номер в формате +7..."

            state["phone"] = phone
            order_id = create_order(
                channel,
                external_user_id,
                state.get("name", ""),
                state.get("phone", ""),
                state.get("product_interest", ""),
                shop_id,
            )
            await notify_shop_owner(order_id, state, channel, external_user_id, shop_id)
            await clear_order_state(user_id)
            # Stop this finished order's products from bleeding into the next one.
            await clear_product_context(user_id)
            return "Заказ принят. Менеджер скоро свяжется с вами для подтверждения."

        await clear_order_state(user_id)
        return None
    except Exception as e:
        log.error(f"Order flow failed: {e}")
        return None
