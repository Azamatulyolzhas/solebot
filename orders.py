import difflib
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


def normalize_phone(message: str) -> str | None:
    """Validate + normalize a Kazakhstan phone to +7XXXXXXXXXX, or None.

    Accepts 11 digits starting 7/8 (7700…, 8700…) or a 10-digit national number
    starting with 7. A random 10-digit string like '5676678657' is rejected — the
    bug where any 10–15 digits passed straight into an order."""
    digits = re.sub(r"\D", "", message or "")
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "7":
        return "+7" + digits
    return None


def _is_valid_name(name: str) -> bool:
    """A name must contain letters — a phone number typed into the name slot
    (e.g. '87765645633') must be rejected, not stored as the customer's name."""
    cleaned = (name or "").strip()
    if not cleaned or cleaned.isdigit():
        return False
    letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", cleaned)
    return len(letters) >= 2


_CONFIRM_WORDS = frozenset({
    "да", "ага", "угу", "ок", "окей", "ok", "okay", "yes", "верно",
    "подтверждаю", "подтвердить", "оформляй", "оформляйте",
    "оформляем", "точно", "правильно", "согласен", "согласна", "давай",
    "давайте", "конечно", "ес",
})
_CANCEL_WORDS = frozenset({
    "нет", "неверно", "неправильно", "отмена", "отменить", "отмени",
    "стоп", "cancel", "no", "nope",
})
_CANCEL_PHRASES = ("не верно", "не правильно", "не надо", "не нужно", "не так")


def _norm_tokens(message: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", (message or "").lower()).split()


def _is_confirm(message: str) -> bool:
    return any(w in _CONFIRM_WORDS for w in _norm_tokens(message))


def _is_cancel(message: str) -> bool:
    text = re.sub(r"[^\w\s]", " ", (message or "").lower())
    if any(w in _CANCEL_WORDS for w in text.split()):
        return True
    return any(p in text for p in _CANCEL_PHRASES)


def _order_summary(state: dict) -> str:
    return (
        "Проверьте заказ:\n"
        f"• Товар: {state.get('product_interest') or '—'}\n"
        f"• Имя: {state.get('name') or '—'}\n"
        f"• Телефон: {state.get('phone') or '—'}\n\n"
        "Всё верно? Напишите «да» для подтверждения или «нет», чтобы отменить."
    )


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


def _hit_matches_query(query: str, product: dict) -> bool:
    """True only if the product actually corresponds to the customer's words
    (transliteration + fuzzy aware). Guards the confirm step against binding the
    order to an unrelated top search hit — 'найк айр макс' must never resolve to a
    'Куртка Carhartt' just because a filler word matched a substring."""
    from products import _content_words, _translit_cyr_to_lat

    name = (product.get("name") or "").lower()
    name_tokens = [t for t in re.findall(r"[a-zа-яё0-9]+", name) if len(t) >= 3]
    if not name_tokens:
        return False
    for w in _content_words(query):
        if len(w) < 3:
            continue
        cand = _translit_cyr_to_lat(w)
        if not cand:
            continue
        if any(cand in tok or tok in cand for tok in name_tokens):
            return True
        if difflib.get_close_matches(cand, name_tokens, n=1, cutoff=0.72):
            return True
    return False


# Order-flow words that are conversational filler, not part of a product name.
_ORDER_FILLER = {
    "хочу", "купить", "куплю", "оформить", "оформляем", "оформи", "оформите",
    "оформили", "заказ", "заказать", "брать", "беру", "возьму", "пожалуйста",
}


def _trim_to_product_phrase(text: str) -> str:
    """Drop a TRAILING run of filler/stopwords so a confirm answer reads as the
    product, not the whole sentence: 'найк айр макс вы же не оформили заказ' →
    'найк айр макс'. Only trailing words are removed, so 'кроссовки для бега'
    (a mid-phrase stopword) stays intact."""
    from products import STOP_WORDS

    words = text.split()
    while words:
        key = re.sub(r"[^\w-]", "", words[-1].lower())
        if key in STOP_WORDS or key in _ORDER_FILLER:
            words.pop()
        else:
            break
    return " ".join(words).strip() or text


async def _resolve_confirmed_product(user_id: str, user_message: str, shop_id: int | None = None) -> str | None:
    """Resolve the product the customer named at the confirm step.

    - Clean catalog match → the catalog label.
    - Search found *something* (so the words relate to the catalog) but no clean
      top match → keep the literal text, trimmed of trailing filler. A human
      manager will read 'найк айр макс' fine; we must not dead-end a real product
      the search just couldn't pin (cross-script).
    - Search found NOTHING → return None. Pure garbage like 'Роло' is not a product
      we carry, so the caller re-asks instead of binding the order to junk."""
    text = user_message.strip()
    try:
        from ai import _product_label
        from products import get_relevant_products
        hits = await get_relevant_products(text, shop_id=resolve_shop_id(shop_id))
    except Exception:
        log.exception("Confirm-product resolution failed for %s", user_id)
        return _trim_to_product_phrase(text)
    if not hits:
        return None
    if _hit_matches_query(text, hits[0]):
        return _product_label(hits[0])
    return _trim_to_product_phrase(text)


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
            if not product_interest:
                # No catalog match at all → don't bind the order to junk, re-ask.
                return (
                    "Не нашёл такой товар в каталоге. Напишите точное название или "
                    "модель — или загляните в список выше."
                )
            state["product_interest"] = product_interest
            state["step"] = "name"
            await set_order_state(user_id, state)
            return "Отлично, оформим заказ. Напишите, пожалуйста, ваше имя."

        if step == "name":
            name = user_message.strip()
            if not _is_valid_name(name):
                return "Похоже, это не имя. Напишите, пожалуйста, как к вам обращаться."

            state["name"] = name
            state["step"] = "phone"
            await set_order_state(user_id, state)
            return "Спасибо. Теперь отправьте номер телефона для связи."

        if step == "phone":
            phone = normalize_phone(user_message)
            if not phone:
                return "Похоже, это не номер. Отправьте казахстанский номер в формате +7XXXXXXXXXX."

            state["phone"] = phone
            state["step"] = "confirm"
            await set_order_state(user_id, state)
            # Echo the order back for an explicit confirmation BEFORE creating it.
            return _order_summary(state)

        if step == "confirm":
            if _is_cancel(user_message):
                await clear_order_state(user_id)
                await clear_product_context(user_id)
                return "Отменил оформление. Напишите «хочу купить», когда будете готовы 🙂"
            if not _is_confirm(user_message):
                return "Напишите «да», если всё верно, или «нет», чтобы отменить."

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
