import logging

from config import MANAGER_TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)


async def notify_manager(order_id: int | None, state: dict, channel: str, external_user_id: str) -> None:
    try:
        from telegram_bot import tg_bot

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
