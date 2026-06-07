import logging

import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message

from ai import ask_ai
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_URL
from shops import get_all_active_telegram_shops, get_shop_by_id, get_shop_by_webhook_secret

log = logging.getLogger(__name__)

TELEGRAM_WEBHOOK = TELEGRAM_WEBHOOK_URL

tg_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
tg_dp = Dispatcher() if TELEGRAM_BOT_TOKEN else None
shop_bots: dict[str, tuple[Bot, Dispatcher, dict]] = {}

if tg_dp:

    @tg_dp.message(CommandStart())
    async def tg_start(msg: Message):
        await msg.answer(
            "Привет! Я AI-консультант магазина.\n"
            "Спросите о любом товаре — проверю наличие и цену по каталогу."
        )

    @tg_dp.message()
    async def tg_message(msg: Message):
        user_id = f"tg_{msg.from_user.id}"
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        reply = await ask_ai(user_id, msg.text or "")
        await msg.answer(reply)


async def register_shop_bot(shop: dict) -> None:
    try:
        secret = shop.get("tg_webhook_secret")
        token = shop.get("tg_token")
        shop_id = shop.get("id")
        if not secret or not token or not shop_id:
            return

        bot = Bot(token=token)
        dp = Dispatcher()

        @dp.message(CommandStart())
        async def shop_start(msg: Message):
            fresh = get_shop_by_id(shop_id) or {}
            bot_role = fresh.get("bot_role") or "консультант"
            await msg.answer(
                f"Привет! Я {bot_role} магазина {fresh.get('name')}.\n"
                "Спросите о товаре — проверю наличие и цену по каталогу."
            )

        @dp.message()
        async def shop_message(msg: Message):
            from billing import check_message_quota, is_subscription_active, quota_exceeded_message

            if not is_subscription_active(shop_id):
                await msg.answer("⚠️ Подписка магазина истекла. Пожалуйста, обратитесь к владельцу.")
                return
            allowed, used, limit = check_message_quota(shop_id)
            if not allowed:
                await msg.answer(f"⚠️ {quota_exceeded_message(used, limit)}")
                return
            user_id = f"tg_{shop_id}_{msg.from_user.id}"
            await msg.bot.send_chat_action(msg.chat.id, "typing")
            reply = await ask_ai(user_id, msg.text or "", shop_id=shop_id)
            await msg.answer(reply)

        fresh = get_shop_by_id(shop_id) or shop
        shop_bots[secret] = (bot, dp, fresh)

        if TELEGRAM_WEBHOOK:
            webhook_url = TELEGRAM_WEBHOOK.rstrip("/") + f"/tg/{secret}/webhook"
            await bot.set_webhook(webhook_url, drop_pending_updates=True)
            log.info(f"Telegram webhook установлен для {fresh.get('name')}: {webhook_url}")
    except Exception as e:
        log.error(f"Register shop bot failed for shop {shop.get('id')}: {e}")


async def setup_shop_bots() -> None:
    try:
        for stub in get_all_active_telegram_shops():
            shop = get_shop_by_id(stub["id"]) or stub
            await register_shop_bot(shop)
        log.info(f"Registered {len(shop_bots)} shop Telegram bots")
    except Exception as e:
        log.error(f"Setup shop bots failed: {e}")


async def unregister_shop_bot(shop_id: int) -> None:
    """Remove shop bot from memory and delete Telegram webhook."""
    secrets = [k for k, (_, _, s) in shop_bots.items() if s.get("id") == shop_id]
    for secret in secrets:
        bot, _, _ = shop_bots.pop(secret)
        try:
            await bot.delete_webhook()
            await bot.session.close()
        except Exception as e:
            log.error(f"Unregister shop bot failed for shop {shop_id}: {e}")


async def close_shop_bots() -> None:
    for bot, _, shop in shop_bots.values():
        try:
            await bot.session.close()
        except Exception as e:
            log.error(f"Close shop bot failed for shop {shop.get('id')}: {e}")
    shop_bots.clear()


async def setup_default_webhook() -> None:
    if tg_bot and TELEGRAM_WEBHOOK:
        try:
            webhook_url = TELEGRAM_WEBHOOK.rstrip("/") + "/tg/webhook"
            await tg_bot.set_webhook(webhook_url, drop_pending_updates=True)
            log.info(f"Telegram webhook установлен: {webhook_url}")
        except Exception as e:
            log.error(f"Ошибка установки webhook: {e}")


async def close_default_bot() -> None:
    if tg_bot:
        try:
            await tg_bot.session.close()
        except Exception:
            pass


async def process_default_update(data: dict) -> None:
    if not tg_bot or not tg_dp:
        raise RuntimeError("Telegram не настроен")
    update = types.Update.model_validate(data)
    await tg_dp.feed_update(tg_bot, update)


async def process_shop_update(webhook_secret: str, data: dict) -> dict:
    if webhook_secret not in shop_bots:
        stub = get_shop_by_webhook_secret(webhook_secret)
        if not stub:
            raise KeyError("Shop not found")
        shop = get_shop_by_id(stub["id"]) or stub
        await register_shop_bot(shop)

    bot, dp, shop = shop_bots[webhook_secret]
    fresh = get_shop_by_id(shop["id"])
    if fresh:
        shop_bots[webhook_secret] = (bot, dp, fresh)
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return fresh or shop
