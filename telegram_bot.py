import logging

import httpx
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ai import ask_ai, _ORDER_HINT, _HANDOFF_HINT
from orders import ORDER_CONFIRM_MARKER
from cache import clear_chat_context, clear_handoff_state, get_handoff_state, set_handoff_state
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL
from shops import get_all_active_telegram_shops, get_shop_by_id, get_shop_by_webhook_secret

log = logging.getLogger(__name__)

TELEGRAM_WEBHOOK = TELEGRAM_WEBHOOK_URL

tg_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
tg_dp = Dispatcher() if TELEGRAM_BOT_TOKEN else None
shop_bots: dict[str, tuple[Bot, Dispatcher, dict]] = {}

if tg_dp:

    @tg_dp.message(CommandStart())
    async def tg_start(msg: Message):
        user_id = f"tg_{msg.from_user.id}"
        await clear_chat_context(user_id)
        await msg.answer(
            "Привет! Я AI-консультант магазина.\n"
            "Спросите о любом товаре — проверю наличие и цену по каталогу."
        )

    @tg_dp.message()
    async def tg_message(msg: Message):
        from shops import get_default_shop_id

        user_id = f"tg_{msg.from_user.id}"
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        # Default Telegram bot targets the default shop — pass explicitly (P3 item 3).
        reply = await ask_ai(user_id, msg.text or "", shop_id=get_default_shop_id())
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
            user_id = f"tg_{shop_id}_{msg.from_user.id}"
            await clear_chat_context(user_id)
            await clear_handoff_state(user_id)
            fresh = get_shop_by_id(shop_id) or {}
            bot_role = fresh.get("bot_role") or "консультант"
            await msg.answer(
                f"Привет! Я {bot_role} магазина {fresh.get('name')}.\n"
                "Спросите о товаре — проверю наличие и цену по каталогу."
            )

        @dp.message(Command("operator"))
        async def shop_operator(msg: Message):
            user_id = f"tg_{shop_id}_{msg.from_user.id}"
            already = await get_handoff_state(user_id)
            if already:
                await msg.answer("Менеджер уже уведомлён и скоро свяжется с вами.")
                return
            await set_handoff_state(user_id)
            fresh = get_shop_by_id(shop_id) or {}
            from notifications import notify_handoff
            from conversations import split_user_id
            _, ext_id = split_user_id(user_id)
            await notify_handoff(
                user_id, msg.text or "/operator", "tg", ext_id,
                shop_id, reason="operator",
            )
            await msg.answer(
                "Понял, передаю вас менеджеру. Он скоро свяжется с вами. "
                "Чтобы снова общаться с ботом — напишите /start."
            )

        _BUY_KB = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Хочу купить", callback_data="buy")
        ]])
        _HANDOFF_KB = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👤 Позвать менеджера", callback_data="call_manager"),
            InlineKeyboardButton(text="🔎 Искать дальше", callback_data="search_more"),
        ]])
        _CONFIRM_KB = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="order_confirm"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="order_cancel"),
        ]])

        @dp.message()
        async def shop_message(msg: Message):
            # is_subscription_active + check_message_quota used to run here AND
            # again inside ask_ai (4 redundant DB queries per inbound message).
            # ask_ai is the single gate now — on exhaustion it returns
            # CUSTOMER_UNAVAILABLE_TEXT and dispatches the owner alert itself.
            user_id = f"tg_{shop_id}_{msg.from_user.id}"
            await msg.bot.send_chat_action(msg.chat.id, "typing")
            reply = await ask_ai(user_id, msg.text or "", shop_id=shop_id)

            if _HANDOFF_HINT in reply:
                text = reply.replace(_HANDOFF_HINT, "").rstrip()
                await msg.answer(text, reply_markup=_HANDOFF_KB)
            elif _ORDER_HINT in reply:
                text = reply.replace(_ORDER_HINT, "").rstrip()
                await msg.answer(text, reply_markup=_BUY_KB)
            elif ORDER_CONFIRM_MARKER in reply:
                await msg.answer(reply, reply_markup=_CONFIRM_KB)
            else:
                await msg.answer(reply)

        @dp.callback_query(F.data == "call_manager")
        async def shop_call_manager(cb: CallbackQuery):
            from cache import set_handoff_state
            from notifications import notify_handoff

            user_id = f"tg_{shop_id}_{cb.from_user.id}"
            await cb.answer()
            await set_handoff_state(user_id)
            try:
                await notify_handoff(
                    user_id, "(клиент нажал «Позвать менеджера»)", "tg",
                    str(cb.from_user.id), shop_id, reason="manual",
                )
            except Exception:
                log.exception("Handoff notify failed shop=%s", shop_id)
            await cb.message.answer(
                "Передаю вас менеджеру — он скоро свяжется 🙌 "
                "Чтобы вернуться к боту, напишите «бот»."
            )

        @dp.callback_query(F.data == "search_more")
        async def shop_search_more(cb: CallbackQuery):
            await cb.answer()
            await cb.message.answer("Хорошо! Назовите категорию или модель — поищу ещё 🙂")

        @dp.callback_query(F.data == "buy")
        async def shop_buy_callback(cb: CallbackQuery):
            from billing import CUSTOMER_UNAVAILABLE_TEXT, check_message_quota, is_subscription_active
            from notifications import notify_owner_quota_exhausted, notify_owner_subscription_expired
            from orders import handle_order_flow

            if not is_subscription_active(shop_id):
                try:
                    await notify_owner_subscription_expired(shop_id)
                except Exception:
                    log.exception("Subscription owner alert failed shop=%s", shop_id)
                await cb.answer(CUSTOMER_UNAVAILABLE_TEXT, show_alert=True)
                return

            allowed, used, limit = check_message_quota(shop_id)
            if not allowed:
                try:
                    await notify_owner_quota_exhausted(shop_id, used, limit)
                except Exception:
                    log.exception("Quota owner alert failed shop=%s", shop_id)
                await cb.answer(CUSTOMER_UNAVAILABLE_TEXT, show_alert=True)
                return

            user_id = f"tg_{shop_id}_{cb.from_user.id}"
            await cb.answer()
            order_reply = await handle_order_flow(user_id, "хочу купить", shop_id=shop_id)
            if order_reply:
                await cb.message.answer(order_reply)

        @dp.callback_query(F.data.in_({"order_confirm", "order_cancel"}))
        async def shop_order_confirm_callback(cb: CallbackQuery):
            from orders import handle_order_flow

            user_id = f"tg_{shop_id}_{cb.from_user.id}"
            await cb.answer()
            answer = "да" if cb.data == "order_confirm" else "нет"
            order_reply = await handle_order_flow(user_id, answer, shop_id=shop_id)
            await cb.message.answer(
                order_reply
                or "Этот заказ уже обработан. Напишите «хочу купить», чтобы оформить новый 🙂"
            )

        fresh = get_shop_by_id(shop_id) or shop
        shop_bots[secret] = (bot, dp, fresh)

        if TELEGRAM_WEBHOOK:
            webhook_url = TELEGRAM_WEBHOOK.rstrip("/") + f"/tg/{secret}/webhook"
            # secret_token = the shop's webhook_secret. Telegram echoes it back
            # in the X-Telegram-Bot-Api-Secret-Token header so the route can
            # reject anything that didn't come from Telegram.
            await bot.set_webhook(webhook_url, drop_pending_updates=True, secret_token=secret)
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
            kwargs = {"drop_pending_updates": True}
            if TELEGRAM_WEBHOOK_SECRET:
                kwargs["secret_token"] = TELEGRAM_WEBHOOK_SECRET
            await tg_bot.set_webhook(webhook_url, **kwargs)
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
