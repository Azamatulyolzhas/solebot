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


def looks_like_order_request(message: str) -> bool:
    text = message.lower()
    triggers = [
        "С…РѕС‡Сѓ РєСѓРїРёС‚СЊ", "РєСѓРїРёС‚СЊ", "Р·Р°РєР°Р·Р°С‚СЊ", "РѕС„РѕСЂРјРёС‚СЊ",
        "Р±РµСЂСѓ", "РІРѕР·СЊРјСѓ", "РѕРїР»Р°С‚РёС‚СЊ", "Р·Р°РєР°Р·",
    ]
    return any(trigger in text for trigger in triggers)

def looks_like_phone(message: str) -> bool:
    digits = re.sub(r"\D", "", message)
    return 10 <= len(digits) <= 15

async def notify_manager(order_id: int | None, state: dict, channel: str, external_user_id: str) -> None:
    try:
        if not tg_bot or not MANAGER_TELEGRAM_CHAT_ID:
            return

        text = (
            "РќРѕРІС‹Р№ Р·Р°РєР°Р· SoleBot\n"
            f"ID: {order_id or 'unknown'}\n"
            f"РљР°РЅР°Р»: {channel}\n"
            f"РљР»РёРµРЅС‚: {external_user_id}\n"
            f"РРјСЏ: {state.get('name', '')}\n"
            f"РўРµР»РµС„РѕРЅ: {state.get('phone', '')}\n"
            f"РРЅС‚РµСЂРµСЃ: {state.get('product_interest', '')}"
        )
        await tg_bot.send_message(MANAGER_TELEGRAM_CHAT_ID, text)
    except Exception as e:
        log.error(f"Manager notification failed: {e}")

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
            return "РћС‚Р»РёС‡РЅРѕ, РѕС„РѕСЂРјРёРј Р·Р°РєР°Р·. РќР°РїРёС€РёС‚Рµ, РїРѕР¶Р°Р»СѓР№СЃС‚Р°, РІР°С€Рµ РёРјСЏ."

        step = state.get("step")
        if step == "name":
            name = user_message.strip()
            if len(name) < 2:
                return "РќР°РїРёС€РёС‚Рµ, РїРѕР¶Р°Р»СѓР№СЃС‚Р°, РёРјСЏ С‡СѓС‚СЊ РїРѕРґСЂРѕР±РЅРµРµ."

            state["name"] = name
            state["step"] = "phone"
            await set_order_state(user_id, state)
            return "РЎРїР°СЃРёР±Рѕ. РўРµРїРµСЂСЊ РѕС‚РїСЂР°РІСЊС‚Рµ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР° РґР»СЏ СЃРІСЏР·Рё."

        if step == "phone":
            phone = user_message.strip()
            if not looks_like_phone(phone):
                return "РџРѕС…РѕР¶Рµ, СЌС‚Рѕ РЅРµ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР°. РћС‚РїСЂР°РІСЊС‚Рµ РЅРѕРјРµСЂ РІ С„РѕСЂРјР°С‚Рµ +7..."

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
            return "Р—Р°РєР°Р· РїСЂРёРЅСЏС‚. РњРµРЅРµРґР¶РµСЂ СЃРєРѕСЂРѕ СЃРІСЏР¶РµС‚СЃСЏ СЃ РІР°РјРё РґР»СЏ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ."

        await clear_order_state(user_id)
        return None
    except Exception as e:
        log.error(f"Order flow failed: {e}")
        return None
