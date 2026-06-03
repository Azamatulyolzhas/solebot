import logging
import re

from cache import clear_order_state, get_order_state, set_order_state
from config import USE_POSTGRES
from conversations import split_user_id
from db import db_placeholder, execute_write, fetch_all, fetch_one_value
from notifications import notify_manager
from shops import resolve_shop_id

log = logging.getLogger(__name__)


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
            return row["id"] if row else None

        execute_write(
            f"""
            INSERT INTO orders
                (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, status)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """,
            (shop_id, channel, external_user_id, customer_name, customer_phone, product_interest, "new"),
        )
        return fetch_one_value("SELECT MAX(id) FROM orders")
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
    text = message.lower()
    triggers = [
        "хочу купить", "купить", "заказать", "оформить",
        "беру", "возьму", "оплатить", "заказ",
    ]
    return any(trigger in text for trigger in triggers)


def looks_like_phone(message: str) -> bool:
    digits = re.sub(r"\D", "", message)
    return 10 <= len(digits) <= 15


async def handle_order_flow(user_id: str, user_message: str, shop_id: int | None = None) -> str | None:
    try:
        channel, external_user_id = split_user_id(user_id)
        state = await get_order_state(user_id)

        if state is None:
            if not looks_like_order_request(user_message):
                return None

            await set_order_state(user_id, {
                "step": "name",
                "product_interest": user_message.strip(),
            })
            return "Отлично, оформим заказ. Напишите, пожалуйста, ваше имя."

        step = state.get("step")
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
            await notify_manager(order_id, state, channel, external_user_id)
            await clear_order_state(user_id)
            return "Заказ принят. Менеджер скоро свяжется с вами для подтверждения."

        await clear_order_state(user_id)
        return None
    except Exception as e:
        log.error(f"Order flow failed: {e}")
        return None
