import csv
import io
import re
import logging

from config import USE_POSTGRES
from db import db_placeholder, execute_write, fetch_all, fetch_one_value, get_db
from schema import ensure_app_tables
from shops import resolve_shop_id

log = logging.getLogger(__name__)


def list_products(limit: int = 100, offset: int = 0, shop_id: int | None = None) -> list[dict]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    return fetch_all(
        f"""
        SELECT id, brand, model, colorway, size, quantity, price, category, gender
        FROM sneakers
        WHERE shop_id = {ph}
        ORDER BY brand, model, colorway, size
        LIMIT {ph} OFFSET {ph}
        """,
        (shop_id, limit, offset),
    )


def parse_product_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    required = {"brand", "model", "size", "quantity", "price"}
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    missing = required - headers
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

    products = []
    for line_no, row in enumerate(reader, start=2):
        try:
            brand = (row.get("brand") or "").strip()
            model = (row.get("model") or "").strip()
            if not brand or not model:
                raise ValueError("brand/model are required")

            products.append({
                "brand": brand,
                "model": model,
                "colorway": (row.get("colorway") or "").strip() or None,
                "size": float(str(row.get("size", "")).replace(",", ".")),
                "quantity": int(row.get("quantity", 0)),
                "price": int(row.get("price", 0)),
                "category": (row.get("category") or "").strip() or None,
                "gender": (row.get("gender") or "").strip() or None,
            })
        except Exception as e:
            raise ValueError(f"Invalid row {line_no}: {e}") from e

    if not products:
        raise ValueError("CSV has no products")
    return products

def validate_product_csv(content: bytes) -> dict:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        return {"valid": False, "products": [], "errors": [f"File encoding error: {e}"]}

    reader = csv.DictReader(io.StringIO(text))
    required = {"brand", "model", "size", "quantity", "price"}
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    missing = required - headers
    errors = []
    products = []

    if missing:
        errors.append(f"Missing columns: {', '.join(sorted(missing))}")
        return {"valid": False, "products": [], "errors": errors}

    for line_no, row in enumerate(reader, start=2):
        try:
            brand = (row.get("brand") or "").strip()
            model = (row.get("model") or "").strip()
            if not brand:
                raise ValueError("brand is required")
            if not model:
                raise ValueError("model is required")

            size = float(str(row.get("size", "")).replace(",", "."))
            quantity = int(row.get("quantity", 0))
            price = int(row.get("price", 0))
            if quantity < 0:
                raise ValueError("quantity cannot be negative")
            if price <= 0:
                raise ValueError("price must be greater than 0")

            products.append({
                "brand": brand,
                "model": model,
                "colorway": (row.get("colorway") or "").strip() or None,
                "size": size,
                "quantity": quantity,
                "price": price,
                "category": (row.get("category") or "").strip() or None,
                "gender": (row.get("gender") or "").strip() or None,
            })
        except Exception as e:
            errors.append(f"Row {line_no}: {e}")

    if not products and not errors:
        errors.append("CSV has no products")

    return {"valid": not errors, "products": products, "errors": errors}

def products_to_csv(products: list[dict]) -> str:
    output = io.StringIO()
    fieldnames = ["brand", "model", "colorway", "size", "quantity", "price", "category", "gender"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for item in products:
        writer.writerow({name: item.get(name, "") for name in fieldnames})
    return output.getvalue()

def import_products(products: list[dict], replace: bool = False, shop_id: int | None = None) -> int:
    ensure_app_tables()
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    conn = get_db()
    try:
        if replace and USE_POSTGRES:
            conn.execute(f"DELETE FROM orders WHERE shop_id = {ph}", (shop_id,))
            conn.execute(f"DELETE FROM sneakers WHERE shop_id = {ph}", (shop_id,))
        elif replace:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM sneakers")

        for item in products:
            if USE_POSTGRES and replace:
                conn.execute(
                    f"""
                    INSERT INTO sneakers
                        (shop_id, brand, model, colorway, size, quantity, price, category, gender)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        shop_id,
                        item["brand"],
                        item["model"],
                        item["colorway"],
                        item["size"],
                        item["quantity"],
                        item["price"],
                        item["category"],
                        item["gender"],
                    ),
                )
            elif USE_POSTGRES:
                conn.execute(
                    f"""
                    DELETE FROM sneakers
                    WHERE shop_id = {ph}
                      AND LOWER(brand) = LOWER({ph})
                      AND LOWER(model) = LOWER({ph})
                      AND COALESCE(LOWER(colorway), '') = COALESCE(LOWER({ph}), '')
                      AND size = {ph}
                    """,
                    (shop_id, item["brand"], item["model"], item["colorway"], item["size"]),
                )
                conn.execute(
                    f"""
                    INSERT INTO sneakers
                        (shop_id, brand, model, colorway, size, quantity, price, category, gender)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        shop_id,
                        item["brand"],
                        item["model"],
                        item["colorway"],
                        item["size"],
                        item["quantity"],
                        item["price"],
                        item["category"],
                        item["gender"],
                    ),
                )
            else:
                conn.execute(
                    f"""
                    DELETE FROM sneakers
                    WHERE LOWER(brand) = LOWER({ph})
                      AND LOWER(model) = LOWER({ph})
                      AND COALESCE(LOWER(colorway), '') = COALESCE(LOWER({ph}), '')
                      AND size = {ph}
                    """,
                    (item["brand"], item["model"], item["colorway"], item["size"]),
                )
                conn.execute(
                    f"""
                    INSERT INTO sneakers
                        (brand, model, colorway, size, quantity, price, category, gender)
                    VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                    """,
                    (
                        item["brand"],
                        item["model"],
                        item["colorway"],
                        item["size"],
                        item["quantity"],
                        item["price"],
                        item["category"],
                        item["gender"],
                    ),
                )
        conn.commit()
        return len(products)
    finally:
        conn.close()

def replace_products(products: list[dict]) -> int:
    return import_products(products, replace=True)


def update_product(product_id: int, price: int | None = None, quantity: int | None = None, shop_id: int | None = None) -> bool:
    """Обновить цену и/или количество конкретного SKU."""
    if price is None and quantity is None:
        return False
    shop_id = resolve_shop_id(shop_id)
    ph = db_placeholder()
    sets = []
    params: list = []
    if price is not None:
        sets.append(f"price = {ph}")
        params.append(price)
    if quantity is not None:
        sets.append(f"quantity = {ph}")
        params.append(quantity)
    params.extend([product_id, shop_id])
    rows = execute_write(
        f"UPDATE sneakers SET {', '.join(sets)} WHERE id = {ph} AND shop_id = {ph}",
        params,
        fetch_one=False,
    )
    return True


# ── Константы ──────────────────────────────────────────────────────────────────

STOP_WORDS = {
    "есть", "ли", "какие", "какой", "какая", "какое", "хочу", "нужны", "нужен",
    "можно", "подскажи", "скажи", "покажи", "что", "это", "мне", "для", "вы",
    "есть", "нет", "как", "про", "по", "на", "из",
}

BROWSE_TERMS = {
    "кроссы", "кроссовки", "кроссовок", "обувь", "sneakers", "shoes", "кросс",
    "ассортимент", "каталог", "модели", "что есть", "все модели",
}

# Алиасы: то, что пишет клиент → реальное название в БД
ALIASES: dict[str, str] = {
    "af1": "air force 1",
    "af 1": "air force 1",
    "аф1": "air force 1",
    "аф 1": "air force 1",
    "nb": "new balance",
    "нб": "new balance",
    "aj1": "air jordan 1",
    "aj 1": "air jordan 1",
    "sb": "dunk",
    "ub": "ultraboost",
    "nm": "nmd",
    "yzy": "yeezy",
    "изи": "yeezy",
    "иизи": "yeezy",
    "dunk low": "dunk low",
    "данк": "dunk",
    "данки": "dunk",
    "форсы": "air force",
    "форс": "air force",
    "джорданы": "jordan",
    "джордан": "jordan",
    "самба": "samba",
    "стэн смит": "stan smith",
    "стэнсмит": "stan smith",
}

# Максимум символов в промпте под каталог — ~1800 токенов
CATALOG_CHAR_LIMIT = 3000
# Сколько SKU вставляем при точном запросе
SKU_DETAIL_LIMIT = 20


# ── Вспомогательные ────────────────────────────────────────────────────────────

def normalize_query(query: str) -> str:
    """Раскрываем алиасы: 'af1 42' → 'air force 1 42'."""
    q = query.lower().strip()
    for alias, expansion in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in q:
            q = q.replace(alias, expansion)
    return q


def extract_requested_size(query: str) -> float | None:
    match = re.search(r"\b(?:р(?:азмер)?\.?\s*)?([3-4][0-9](?:[.,]5)?)\b", query.lower())
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def is_browse_query(query: str) -> bool:
    q = query.lower()
    if any(term in q for term in BROWSE_TERMS):
        return True
    words = [w for w in re.findall(r"[\w-]+", q) if len(w) > 2 and w not in STOP_WORDS]
    return not words and extract_requested_size(query) is None


# ── Запросы к БД ───────────────────────────────────────────────────────────────

def list_in_stock_brands(shop_id: int | None = None) -> list[str]:
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    rows = fetch_all(
        f"SELECT DISTINCT brand FROM sneakers WHERE shop_id = {ph} AND quantity > 0 ORDER BY brand",
        (shop_id,),
    )
    return [row["brand"] for row in rows]


def get_models_summary(shop_id: int | None = None, limit: int = 60) -> list[dict]:
    """Уникальные модели, сгруппированные — для обзора каталога.

    Работает при любом размере БД: GROUP BY не тащит все SKU.
    """
    ph = db_placeholder()
    shop_id = resolve_shop_id(shop_id)
    if USE_POSTGRES:
        return fetch_all(
            f"""
            SELECT brand, model,
                   MIN(price)  AS min_price,
                   MAX(price)  AS max_price,
                   STRING_AGG(DISTINCT CAST(size AS TEXT), ',' ORDER BY CAST(size AS TEXT)) AS sizes,
                   SUM(quantity) AS total_qty
            FROM sneakers
            WHERE shop_id = {ph} AND quantity > 0
            GROUP BY brand, model
            ORDER BY brand, model
            LIMIT {ph}
            """,
            (shop_id, limit),
        )
    return fetch_all(
        f"""
        SELECT brand, model,
               MIN(price) AS min_price,
               MAX(price) AS max_price,
               GROUP_CONCAT(DISTINCT CAST(CAST(size AS INTEGER) AS TEXT)) AS sizes,
               SUM(quantity) AS total_qty
        FROM sneakers
        WHERE shop_id = {ph} AND quantity > 0
        GROUP BY brand, model
        ORDER BY brand, model
        LIMIT {ph}
        """,
        (shop_id, limit),
    )


def search_models(words: list[str], shop_id: int) -> list[tuple[str, str]]:
    """Шаг 1: ищем уникальные (brand, model) по ключевым словам.

    Возвращает только пары без всех SKU — дёшево на большой БД.
    """
    if not words:
        return []
    ph = db_placeholder()
    parts = []
    params: list = []
    for w in words:
        like = f"%{w}%"
        parts.append(
            f"(LOWER(brand) LIKE {ph} OR LOWER(model) LIKE {ph} "
            f"OR LOWER(colorway) LIKE {ph} OR LOWER(category) LIKE {ph})"
        )
        params.extend([like, like, like, like])

    where = " OR ".join(parts)
    rows = fetch_all(
        f"""
        SELECT DISTINCT brand, model
        FROM sneakers
        WHERE shop_id = {ph} AND quantity > 0 AND ({where})
        ORDER BY brand, model
        LIMIT 15
        """,
        [shop_id, *params],
    )
    return [(r["brand"], r["model"]) for r in rows]


def fetch_skus_for_models(
    models: list[tuple[str, str]], size: float | None, shop_id: int, limit: int = SKU_DETAIL_LIMIT
) -> list[dict]:
    """Шаг 2: по найденным моделям берём конкретные SKU (с размером если нужен)."""
    if not models:
        return []
    ph = db_placeholder()
    conds = []
    params: list = []
    for brand, model in models:
        conds.append(f"(LOWER(brand) = {ph} AND LOWER(model) = {ph})")
        params.extend([brand.lower(), model.lower()])

    model_where = " OR ".join(conds)
    size_filter = f"AND size = {ph}" if size is not None else ""
    if size is not None:
        params.append(size)
    params.extend([shop_id, limit])

    return fetch_all(
        f"""
        SELECT brand, model, colorway, size, price, quantity, category, gender
        FROM sneakers
        WHERE ({model_where}) {size_filter}
          AND shop_id = {ph} AND quantity > 0
        ORDER BY brand, model, size
        LIMIT {ph}
        """,
        params,
    )


# ── Форматирование контекста ────────────────────────────────────────────────────

def _fmt_models_block(rows: list[dict], header: str = "") -> str:
    lines = [header] if header else []
    for r in rows:
        price = r.get("min_price") or r.get("price") or 0
        max_p = r.get("max_price")
        price_str = f"от {price}₸" if max_p and max_p != price else f"{price}₸"
        sizes = r.get("sizes") or "—"
        qty = r.get("total_qty") or r.get("quantity") or 0
        lines.append(f"{r['brand']} {r['model']}|{price_str}|р.{sizes}|остаток {qty}")
    return "\n".join(lines)


def _fmt_skus_block(items: list[dict]) -> str:
    lines = []
    for s in items:
        colorway = s.get("colorway") or ""
        lines.append(
            f"  {s['brand']} {s['model']} {colorway}|р.{s['size']}|{s['price']}₸|qty {s['quantity']}"
        )
    return "\n".join(lines)


def _trim_to_limit(text: str, limit: int = CATALOG_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    out = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > limit:
            out.append("... (показаны первые модели)")
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


# ── Главная функция RAG ────────────────────────────────────────────────────────

def build_product_context(query: str, shop_id: int | None = None) -> tuple[str, int]:
    """Строим контекст для промпта. Масштабируется на любой размер каталога.

    Стратегия:
    - Обзорный запрос («кроссы», «что есть») → компактный каталог (grouped, с лимитом токенов)
    - Конкретный запрос («Nike Air Force 42») → только совпадающие SKU + список брендов
    - Всегда ограничиваем размер текста CATALOG_CHAR_LIMIT символами
    """
    shop_id = resolve_shop_id(shop_id)
    normalized = normalize_query(query)
    brands = list_in_stock_brands(shop_id)
    brands_line = f"Бренды на складе: {', '.join(brands)}" if brands else ""

    # ── Обзорный запрос ──────────────────────────────────────────────────────
    if is_browse_query(query):
        models = get_models_summary(shop_id, limit=60)
        if not models:
            return "Каталог пуст.", 0
        body = _fmt_models_block(models, header=brands_line)
        return _trim_to_limit(body), len(models)

    # ── Конкретный запрос ────────────────────────────────────────────────────
    size = extract_requested_size(normalized)
    words = [
        w for w in re.findall(r"[\w-]+", normalized)
        if len(w) > 2 and w not in STOP_WORDS
    ]

    if not words and size is None:
        # нечего искать — возвращаем обзор
        models = get_models_summary(shop_id, limit=60)
        body = _fmt_models_block(models, header=brands_line)
        return _trim_to_limit(body), len(models)

    matched_models = search_models(words, shop_id)

    if not matched_models:
        # ничего не нашли — даём весь каталог сжато
        models = get_models_summary(shop_id, limit=60)
        body = _fmt_models_block(models, header=brands_line)
        return _trim_to_limit(body), 0

    skus = fetch_skus_for_models(matched_models, size, shop_id)
    if not skus:
        # модели есть, но нужного размера нет
        skus = fetch_skus_for_models(matched_models, None, shop_id)

    details = _fmt_skus_block(skus)
    # Добавляем строку брендов чтобы бот не думал что в магазине только Nike
    ctx = f"{brands_line}\n\nПодходящие позиции:\n{details}" if brands_line else f"Подходящие позиции:\n{details}"
    return _trim_to_limit(ctx), len(skus)


# ── search_sneakers (для fallback без Groq) ───────────────────────────────────

def search_sneakers(query: str, shop_id: int | None = None) -> list[dict]:
    """Поиск для fallback — возвращает сырые строки."""
    normalized = normalize_query(query)
    words = [w for w in re.findall(r"[\w-]+", normalized) if len(w) > 2 and w not in STOP_WORDS]
    shop_id = resolve_shop_id(shop_id)

    if not words:
        return []

    ph = db_placeholder()
    conds = []
    params: list = []
    for w in words:
        like = f"%{w}%"
        conds.append(
            f"(LOWER(brand) LIKE {ph} OR LOWER(model) LIKE {ph} "
            f"OR LOWER(colorway) LIKE {ph} OR LOWER(category) LIKE {ph})"
        )
        params.extend([like, like, like, like])

    return fetch_all(
        f"""
        SELECT brand, model, colorway, size, price, quantity
        FROM sneakers
        WHERE shop_id = {ph} AND quantity > 0 AND ({' OR '.join(conds)})
        ORDER BY brand, model, size
        LIMIT 10
        """,
        [shop_id, *params],
    )


def get_relevant_sneakers(query: str, limit: int = SKU_DETAIL_LIMIT, shop_id: int | None = None) -> list[dict]:
    """Прямой доступ для внешнего кода — возвращает SKU по запросу."""
    normalized = normalize_query(query)
    size = extract_requested_size(normalized)
    words = [w for w in re.findall(r"[\w-]+", normalized) if len(w) > 2 and w not in STOP_WORDS]
    shop_id = resolve_shop_id(shop_id)
    matched = search_models(words, shop_id)
    if not matched:
        return []
    return fetch_skus_for_models(matched, size, shop_id, limit=limit)


def format_sneakers_context(items: list[dict], shop_id: int | None = None) -> str:
    """Оставлен для обратной совместимости."""
    if not items:
        return "Нет совпадений в каталоге."
    return _fmt_skus_block(items)
