import json
import logging
import re
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
    clear_chat_context,
    clear_miss_count,
    ensure_session_fresh,
    get_handoff_state,
    get_last_product_interest,
    get_last_shown_products,
    inc_miss_count,
    load_session_history,
    save_session_message,
    set_handoff_state,
    set_last_product_interest,
    set_last_shown_products,
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
    get_all_catalog_products,
    get_catalog_sample,
    get_products_by_ids,
    get_relevant_products,
    is_browse_query,
)
from shops import get_shop_by_id, resolve_shop_id

log = logging.getLogger(__name__)

GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_MAX_TOKENS = 350
GROQ_SEARCH_MAX_TOKENS = 80
GROQ_RETRIES = 3
HISTORY_LIMIT = 8

_FOLLOWUP_MARKERS = (
    "что такое", "что это", "что за", "расскажи", "подробнее", "побольше",
    "какая скидка", "какую скидку", "есть скидка", "сколько стоит", "какая цена",
    "это что", "а это", "про это", "про него", "про неё", "а он", "а она",
    "в наличии", "осталось", "сколько штук",
)

_FORBIDDEN_REPLY_MARKERS = ("скидк", "акци", "бонус", "промокод", "распродаж")

_REJECTION_WORDS = frozenset({
    "нет", "не хочу", "не надо", "не интересно", "нет спасибо",
    "спасибо нет", "не буду", "не нужно", "не нужен", "не нужна",
    "откажусь", "не возьму", "пока нет", "другой раз",
    "no", "nope",
})

_GREETING_WORDS = frozenset({
    "привет", "здравствуй", "здравствуйте", "салем", "сәлем", "салам",
    "hello", "hi", "hey", "start",
})
_GREETING_PHRASES = (
    "добрый день", "доброе утро", "добрый вечер", "доброй ночи",
    "хай", "здарова", "здорово",
)

_ANTI_HALLUCINATION_RULES = (
    "ЖЁСТКИЕ ПРАВИЛА (обязательны всегда):\n"
    "- Товары, цены и остатки — ТОЛЬКО из блока КАТАЛОГ ниже\n"
    "- Не выдумывай товары, бренды и характеристики\n"
    "- Если нужного товара нет в каталоге — скажи честно\n"
    "- Не называй себя выдуманным именем — только роль из настроек\n\n"
    "ФОРМАТ ОТВЕТА:\n"
    "- Пиши простым текстом без markdown: без **, *, #, _\n"
    "- Не более 1 эмодзи на весь ответ\n"
    "- Грамотный русский язык: 'У нас есть', а не 'Нам есть'\n"
    "- Коротко — 2–4 предложения максимум\n\n"
    "ЗАПРЕЩЕНО:\n"
    "- предлагать скидки, акции, бонусы которых нет в каталоге\n"
    "- додумывать назначение товара если его нет в описании\n"
    "- задавать больше одного уточняющего вопроса\n"
    "- отвечать на вопросы не связанные с каталогом магазина\n\n"
    "ПРАВИЛО про характеристики:\n"
    "- упоминай ТОЛЬКО характеристики из поля attributes каждого товара\n"
    "- если attributes пустой — не придумывай характеристики вообще\n"
    "- если клиент спрашивает характеристику которой нет в каталоге — "
    "ответь: 'Уточните у менеджера, напишите хочу купить'"
)

DEFAULT_TONE_PROMPT = (
    "Будь дружелюбным консультантом. Помогай клиенту разобраться и выбрать из того, "
    "что реально есть в магазине."
)

_ORDER_HINT = 'Если хотите оформить заказ — напишите "хочу купить" 🛒'

_PRODUCT_SEARCH_SYSTEM = (
    "Ты помощник по подбору товаров. По запросу клиента выбери релевантные позиции "
    "ТОЛЬКО из каталога ниже.\n"
    "Ответь ТОЛЬКО SKU через запятую (без текста, без пояснений).\n"
    "Если ничего не подходит — ответь ровно: NONE"
)


def is_greeting(message: str) -> bool:
    text = (message or "").strip()
    if not text or len(text) > 50:
        return False
    norm = re.sub(r"[^\w\s]", " ", text.lower())
    norm = " ".join(norm.split())
    if norm in _GREETING_WORDS:
        return True
    if any(norm == p or norm.startswith(p + " ") for p in _GREETING_PHRASES):
        return True
    words = norm.split()
    return len(words) <= 2 and all(w in _GREETING_WORDS for w in words)


def is_rejection(message: str) -> bool:
    text = re.sub(r"[^\w\s]", " ", (message or "").lower()).strip()
    return any(word in text.split() or text == word for word in _REJECTION_WORDS)


def _shop_persona(shop: dict) -> tuple[str, str, str]:
    bot_role = (shop.get("bot_role") or "консультант").strip()
    shop_name = (shop.get("name") or "магазина").strip()
    custom = (shop.get("groq_system_prompt") or "").strip()
    return bot_role, shop_name, custom


def _product_sku_key(product: dict) -> str:
    sku = (product.get("sku") or "").strip()
    if sku:
        return sku
    product_id = product.get("id")
    return f"id:{product_id}" if product_id is not None else ""


def _catalog_line_for_search(product: dict) -> str:
    sku = _product_sku_key(product)
    name = (product.get("name") or "—").strip()
    price = int(product.get("price") or 0)
    qty = int(product.get("quantity") or 0)
    category = (product.get("category") or "").strip()
    desc = (product.get("description") or "").strip()[:100]
    parts = [f"SKU:{sku}", f"name:{name}", f"price:{price}", f"stock:{qty}"]
    if category:
        parts.append(f"category:{category}")
    if desc:
        parts.append(f"desc:{desc}")
    return " | ".join(parts)


def _parse_sku_response(raw: str | None) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    first_line = text.splitlines()[0].strip()
    if first_line.upper() == "NONE":
        return []
    return [token.strip() for token in first_line.split(",") if token.strip()]


def _trim_history(history: list[dict], limit: int = HISTORY_LIMIT) -> list[dict]:
    trimmed = []
    for msg in history[-limit:]:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            trimmed.append({"role": role, "content": content})
    return trimmed


def is_followup_question(message: str, last_interest: str | None = None) -> bool:
    q = (message or "").strip().lower()
    if not q:
        return False
    if any(marker in q for marker in _FOLLOWUP_MARKERS):
        return True
    if last_interest:
        for word in last_interest.lower().split():
            if len(word) >= 4 and word in q:
                return True
    return len(q) < 70 and q.endswith("?")


async def resolve_followup_products(user_id: str, shop_id: int) -> list[dict]:
    stored = await get_last_shown_products(user_id)
    if stored:
        ids = [item["id"] for item in stored if item.get("id") is not None]
        products = get_products_by_ids(shop_id, ids)
        if products:
            return products

    interest = await get_last_product_interest(user_id)
    if not interest:
        return []

    names = {n.strip().lower() for n in interest.split(",") if n.strip()}
    return [
        p for p in get_all_catalog_products(shop_id)
        if (p.get("name") or "").strip().lower() in names
    ]


def _product_facts(products: list[dict]) -> str:
    lines = []
    for p in products[:10]:
        sku = _product_sku_key(p)
        qty = int(p.get("quantity") or 0)
        name = (p.get("name") or "—").strip()
        price = int(p.get("price") or 0)
        stock = "в наличии" if qty > 0 else "нет в наличии"
        desc = (p.get("description") or "").strip()
        category = (p.get("category") or "").strip()
        line = f"- {name} (SKU:{sku}): {price} ₸, остаток: {qty} шт ({stock})"
        if category:
            line += f", категория: {category}"
        if desc:
            line += f", описание: {desc}"
        attrs = p.get("attributes") or {}
        if attrs:
            line += f", характеристики: {json.dumps(attrs, ensure_ascii=False)}"
        lines.append(line)
    return "\n".join(lines) if lines else "- (каталог пуст)"


def validate_groq_reply(
    reply: str,
    products: list[dict],
    *,
    require_product: bool = False,
) -> bool:
    """Универсальная валидация — только то что есть в БД разрешено."""
    if not reply or not products:
        return False

    reply_lower = reply.lower()

    mentioned_prices = {p for p in re.findall(r"\d+", reply) if len(p) >= 3}
    allowed_prices = {str(int(p.get("price") or 0)) for p in products}
    if mentioned_prices - allowed_prices:
        return False

    if any(marker in reply_lower for marker in _FORBIDDEN_REPLY_MARKERS):
        return False

    if require_product:
        allowed_names = [(p.get("name") or "").strip().lower() for p in products if p.get("name")]
        if not any(name in reply_lower for name in allowed_names):
            return False

    return True


def product_not_found_reply() -> str:
    return (
        "К сожалению, не нашёл подходящий товар в каталоге. "
        "Опишите подробнее, что ищете, или напишите название — проверю снова."
    )


def _clean_reply(text: str) -> str:
    """Strip markdown and internal SKU labels from bot replies."""
    # remove bold/italic markdown
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_", r"\1", text, flags=re.DOTALL)
    # remove heading markers
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    # remove SKU labels that leak into customer-facing text
    text = re.sub(r"\s*\(SKU:[^)]+\)", "", text)
    text = re.sub(r"\bSKU:[A-Za-z0-9_\-]+\b", "", text)
    return text.strip()


def _append_order_hint(reply: str) -> str:
    text = _clean_reply(reply or "")
    if not text or _ORDER_HINT in text:
        return text
    return f"{text}\n\n{_ORDER_HINT}"


def product_reply_fallback(products: list[dict]) -> str:
    return _append_order_hint(format_catalog_reply(products))


def greeting_reply(shop_id: int) -> str:
    shop = get_shop_by_id(shop_id) or {}
    bot_role, shop_name, _ = _shop_persona(shop)
    return (
        f"Привет! Я {bot_role} магазина {shop_name}.\n"
        "Спросите о товаре или опишите, что ищете — проверю каталог и подскажу."
    )


async def _groq_messages(
    shop_id: int,
    messages: list[dict],
    api_key: str,
    *,
    temperature: float = 0.4,
    max_tokens: int = GROQ_MAX_TOKENS,
) -> tuple[str | None, dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Groq request failed shop=%s: %s", shop_id, e)
            return None, {}

    data = resp.json()
    usage = data.get("usage") or {}
    if data.get("error"):
        log.error("Groq error shop=%s: %s", shop_id, data["error"])
        return None, usage

    reply = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    return (reply or None), usage


async def find_products_via_ai(
    shop: dict,
    user_query: str,
    all_products: list[dict],
    api_key: str,
    shop_id: int,
    *,
    history: list[dict] | None = None,
) -> list[dict]:
    """Groq selects relevant products by SKU only (temperature=0.1)."""
    if not all_products or not user_query.strip():
        return []

    catalog_lines = [_catalog_line_for_search(p) for p in all_products]
    catalog_text = "\n".join(catalog_lines)

    sku_map: dict[str, dict] = {}
    for product in all_products:
        key = _product_sku_key(product).lower()
        if key:
            sku_map[key] = product

    messages = [
        {"role": "system", "content": f"{_PRODUCT_SEARCH_SYSTEM}\n\nКАТАЛОГ:\n{catalog_text}"},
        *_trim_history(history or []),
        {"role": "user", "content": user_query},
    ]

    raw, _usage = await _groq_messages(
        shop_id,
        messages,
        api_key,
        temperature=0.1,
        max_tokens=GROQ_SEARCH_MAX_TOKENS,
    )
    sku_tokens = _parse_sku_response(raw)
    log.info(
        "AI product search shop=%s query=%r skus=%r raw=%r",
        shop_id, user_query[:80], sku_tokens, (raw or "")[:120],
    )

    found: list[dict] = []
    seen_ids: set = set()
    for token in sku_tokens:
        product = sku_map.get(token.lower())
        if not product:
            continue
        pid = product.get("id")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        found.append(product)

    return found


async def build_product_reply(
    shop_id: int,
    shop: dict,
    products: list[dict],
    user_query: str,
    api_key: str,
    *,
    history: list[dict] | None = None,
    followup: bool = False,
) -> tuple[str, dict, str]:
    """Groq reformulates answer from DB facts only (temperature=0.4)."""
    bot_role, shop_name, custom = _shop_persona(shop)
    tone = custom or DEFAULT_TONE_PROMPT
    catalog_block = "КАТАЛОГ (только эти товары и цены):\n" + _product_facts(products)
    if followup:
        situation = (
            f"Клиент уточняет про товар(ы) из недавнего диалога: {user_query}\n"
            "Ответь на вопрос, опираясь на историю и каталог. "
            "Не запускай новый поиск — используй товары из каталога выше."
        )
        require_product = False
        mode = "ai_followup"
    else:
        situation = (
            f"Клиент спросил: {user_query}\n"
            "Ответь по-человечески, учитывая контекст диалога. "
            "Используй только товары и цены из каталога. Не задавай лишних вопросов."
        )
        require_product = True
        mode = "ai_product"

    system = (
        f"Ты {bot_role} магазина {shop_name}.\n\n"
        f"ТОН И СТИЛЬ:\n{tone}\n\n"
        f"{_ANTI_HALLUCINATION_RULES}\n\n"
        f"{catalog_block}\n\n"
        f"СИТУАЦИЯ:\n{situation}"
    )
    usage: dict = {}
    groq_history = _trim_history(history or [])

    for attempt in range(GROQ_RETRIES):
        reply, usage = await _groq_messages(
            shop_id,
            [
                {"role": "system", "content": system},
                *groq_history,
                {"role": "user", "content": user_query},
            ],
            api_key,
            temperature=0.4 + (attempt * 0.05),
        )
        if reply and validate_groq_reply(reply, products, require_product=require_product):
            return _append_order_hint(reply), usage, mode

        log.warning(
            "Product reply validation failed shop=%s attempt=%s query=%r",
            shop_id, attempt + 1, user_query[:80],
        )

    return product_reply_fallback(products), usage, "catalog_validated_fallback"


async def build_greeting_reply(
    shop_id: int,
    shop: dict,
    user_message: str,
    api_key: str,
    catalog_sample: list[dict],
    *,
    history: list[dict] | None = None,
) -> tuple[str, dict, str]:
    bot_role, shop_name, custom = _shop_persona(shop)
    tone = custom or DEFAULT_TONE_PROMPT
    system = (
        f"Ты {bot_role} магазина {shop_name}.\n\n"
        f"ТОН И СТИЛЬ:\n{tone}\n\n"
        f"{_ANTI_HALLUCINATION_RULES}\n\n"
        "СИТУАЦИЯ:\nКлиент поздоровался. Поздоровайся и спроси что ищет — "
        "одно-два предложения, без перечисления товаров."
    )
    reply, usage = await _groq_messages(
        shop_id,
        [
            {"role": "system", "content": system},
            *_trim_history(history or []),
            {"role": "user", "content": user_message},
        ],
        api_key,
        temperature=0.4,
    )
    if reply:
        return _clean_reply(reply), usage, "ai_greeting"
    return greeting_reply(shop_id), {}, "greeting"


async def ask_ai(
    user_id: str,
    user_message: str,
    shop_id: int | None = None,
    *,
    reset_context: bool = False,
) -> str:
    if not user_message or not user_message.strip():
        return "Напишите, какой товар вас интересует — проверю наличие в каталоге."

    if reset_context:
        await clear_chat_context(user_id)
    else:
        await ensure_session_fresh(user_id)

    shop_id = resolve_shop_id(shop_id)
    if not is_subscription_active(shop_id):
        return "Подписка магазина истекла. Обратитесь к владельцу магазина."

    if await get_handoff_state(user_id):
        return "Менеджер скоро свяжется с вами. Чтобы снова общаться с ботом — напишите /start."

    allowed, used, limit = check_message_quota(shop_id)
    if not allowed:
        return quota_exceeded_message(used, limit)

    api_key = resolve_groq_api_key(shop_id)
    shop = get_shop_by_id(shop_id) or {}

    started_at = time.perf_counter()
    channel, external_user_id = split_user_id(user_id)
    conversation_id = None
    product_count = 0
    history: list[dict] = []

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
        history = await load_session_history(user_id)
        conversation_id = get_or_create_conversation(channel, external_user_id, shop_id)
        if not history and conversation_id is not None:
            history = load_recent_messages(conversation_id, limit=HISTORY_LIMIT)
        save_message(conversation_id, "user", user_message)
        await save_session_message(user_id, "user", user_message)
    except Exception:
        log.exception("Conversation storage failed")
        await save_session_message(user_id, "user", user_message)
        history = await load_session_history(user_id)

    groq_history = _trim_history(history)

    order_reply = await handle_order_flow(user_id, user_message, shop_id)
    if order_reply:
        await save_ai_result(
            user_id, conversation_id, channel, user_message, order_reply,
            started_at, product_count, "order", shop_id=shop_id,
        )
        return order_reply

    catalog_sample = get_catalog_sample(shop_id, limit=10)

    if is_greeting(user_message):
        if api_key:
            reply, usage, mode = await build_greeting_reply(
                shop_id, shop, user_message, api_key, catalog_sample,
                history=groq_history,
            )
        else:
            reply, usage, mode = greeting_reply(shop_id), {}, "greeting"
        await save_ai_result(
            user_id, conversation_id, channel, user_message, reply,
            started_at, product_count, mode, usage=usage, shop_id=shop_id,
        )
        return reply

    usage: dict = {}
    matched: list[dict] = []
    is_followup = False

    if is_browse_query(user_message):
        reply = format_browse_reply(shop_id)
        mode = "catalog_browse"
    else:
        last_interest = await get_last_product_interest(user_id)

        if is_rejection(user_message) and last_interest:
            reply = "Хорошо, понял. Если понадоблюсь — пишите. Ищете что-то другое?"
            mode = "rejection"
            await save_ai_result(
                user_id, conversation_id, channel, user_message, reply,
                started_at, 0, mode, shop_id=shop_id,
            )
            return reply

        if is_followup_question(user_message, last_interest):
            matched = await resolve_followup_products(user_id, shop_id)
            is_followup = bool(matched)
            log.info(
                "Follow-up shop_id=%s products=%s query=%r",
                shop_id, len(matched), user_message[:100],
            )

        if not matched:
            try:
                matched = await get_relevant_products(
                    user_message,
                    shop_id=shop_id,
                    shop=shop,
                    api_key=api_key,
                    history=groq_history,
                )
                log.info(
                    "AI search shop_id=%s hits=%s query=%r",
                    shop_id, len(matched), user_message[:100],
                )
            except Exception:
                log.exception("AI product search failed")

        product_count = len(matched)

        if matched and api_key:
            reply, usage, mode = await build_product_reply(
                shop_id, shop, matched[:8], user_message, api_key,
                history=groq_history,
                followup=is_followup,
            )
        elif matched:
            reply = _append_order_hint(format_catalog_reply(matched))
            mode = "catalog_exact"
        elif not api_key:
            reply = "Для подбора товаров по описанию нужен Groq API ключ в настройках магазина."
            mode = "no_api_key"
        else:
            reply = product_not_found_reply()
            mode = "catalog_not_found"

    if matched:
        interest = ", ".join(item["name"] for item in matched[:3])
        await set_last_product_interest(user_id, interest)
        await set_last_shown_products(user_id, matched[:8])
        await clear_miss_count(user_id)
    elif mode == "catalog_not_found":
        miss = await inc_miss_count(user_id)
        if miss >= 3:
            await set_handoff_state(user_id)
            from conversations import split_user_id as _split
            _, ext_id = _split(user_id)
            from notifications import notify_handoff
            await notify_handoff(
                user_id, user_message, channel, ext_id,
                shop_id, reason="auto",
            )
            reply = (
                "Не могу найти подходящий товар в каталоге. "
                "Передаю вас менеджеру — он скоро свяжется. "
                "Чтобы снова общаться с ботом — напишите /start."
            )

    await save_ai_result(
        user_id, conversation_id, channel, user_message, reply,
        started_at, product_count, mode, usage=usage, shop_id=shop_id,
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
