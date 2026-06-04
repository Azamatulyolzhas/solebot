"""
CommerceML 2.x parser (used by 1С:Розница, 1С:УТ, 1С:Комплексная).

Typical export flow in 1С:
  1. Файл → Обмен данными → Выгрузить данные в формате CommerceML
  2. Upload the resulting .xml file to POST /sync/1c

CommerceML structure we handle:
  <КоммерческаяИнформация>
    <Каталог>
      <Товары>
        <Товар>
          <Ид>uuid</Ид>
          <Наименование>Nike Air Force 1 белый</Наименование>
          <Артикул>NK-AF1-WHT-42</Артикул>
          <ЗначенияРеквизитов>
            <ЗначениеРеквизита>
              <Наименование>Размер</Наименование>
              <Значение>42</Значение>
            </ЗначениеРеквизита>
          </ЗначенияРеквизитов>
        </Товар>
      </Товары>
    </Каталог>
    <ПакетПредложений>
      <Предложения>
        <Предложение>
          <Ид>uuid</Ид>
          <Количество>5</Количество>
          <Цены>
            <Цена><ЦенаЗаЕдиницу>25000</ЦенаЗаЕдиницу></Цена>
          </Цены>
        </Предложение>
      </Предложения>
    </ПакетПредложений>
  </КоммерческаяИнформация>
"""
import re
import logging
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

# Well-known brands (sorted by length desc to catch "New Balance" before "Balance")
KNOWN_BRANDS = sorted([
    "New Balance", "Nike", "Adidas", "Jordan", "Puma", "Reebok",
    "Converse", "Vans", "Asics", "Saucony", "Brooks", "Hoka",
    "Salomon", "Timberland", "Under Armour", "Balenciaga", "Gucci",
    "Louis Vuitton", "Alexander McQueen", "Dior", "Off-White",
    "Yeezy", "Air Jordan",
], key=len, reverse=True)


def _ns(tag: str) -> str:
    """Strip namespace prefix if present."""
    return tag.split("}")[-1] if "}" in tag else tag


def _find(el: ET.Element, *paths: str) -> str:
    """Find text in nested tags, trying each path."""
    for path in paths:
        parts = path.split("/")
        cur = el
        for p in parts:
            cur = next((c for c in cur if _ns(c.tag) == p), None)
            if cur is None:
                break
        if cur is not None and cur.text:
            return cur.text.strip()
    return ""


def _parse_brand_model(name: str) -> tuple[str, str]:
    """Split a product name into (brand, model) by matching known brands."""
    for brand in KNOWN_BRANDS:
        if name.lower().startswith(brand.lower()):
            model = name[len(brand):].strip(" -–—")
            return brand, model or name
    # Fallback: first word as brand
    parts = name.split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else name


def _parse_size(text: str) -> float | None:
    """Extract numeric size from a string like '42', '42.5', 'EU 42'."""
    m = re.search(r"\b(\d{2}(?:[.,]\d)?)\b", text)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_commerceml(xml_bytes: bytes) -> list[dict]:
    """
    Parse a CommerceML XML file and return a list of product dicts
    compatible with import_products().

    Returns list of:
        {brand, model, colorway, size, quantity, price, category, gender}
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    # ── Collect products from <Каталог> ───────────────────────────────────────
    products: dict[str, dict] = {}   # id → product dict

    catalog = next((c for c in root if _ns(c.tag) == "Каталог"), None)
    if catalog is not None:
        товары = next((c for c in catalog if _ns(c.tag) == "Товары"), None)
        if товары is not None:
            for товар in товары:
                if _ns(товар.tag) != "Товар":
                    continue

                uid = _find(товар, "Ид")
                name = _find(товар, "Наименование")
                article = _find(товар, "Артикул")
                if not uid or not name:
                    continue

                brand, model = _parse_brand_model(name)

                # Try to find size in реквизиты
                size_val: float | None = None
                реквизиты = next((c for c in товар if _ns(c.tag) == "ЗначенияРеквизитов"), None)
                if реквизиты is not None:
                    for req in реквизиты:
                        req_name = _find(req, "Наименование").lower()
                        if "размер" in req_name or "size" in req_name:
                            size_val = _parse_size(_find(req, "Значение"))
                            break

                # Fall back: try to parse size from article or name
                if size_val is None and article:
                    size_val = _parse_size(article)
                if size_val is None:
                    # Look for typical size patterns at the end of the name
                    size_val = _parse_size(name[-6:]) if len(name) > 6 else None

                products[uid] = {
                    "brand":    brand,
                    "model":    model,
                    "colorway": "",
                    "size":     size_val or 0.0,
                    "quantity": 0,
                    "price":    0,
                    "category": "lifestyle",
                    "gender":   "unisex",
                    "_article": article,
                }

    # ── Overlay quantities/prices from <ПакетПредложений> ────────────────────
    пакет = next((c for c in root if _ns(c.tag) == "ПакетПредложений"), None)
    if пакет is not None:
        предложения = next((c for c in пакет if _ns(c.tag) == "Предложения"), None)
        if предложения is not None:
            for п in предложения:
                if _ns(п.tag) != "Предложение":
                    continue
                # Ид may be "uuid#variant" — take only uuid part
                uid_raw = _find(п, "Ид")
                uid = uid_raw.split("#")[0]

                qty_text = _find(п, "Количество")
                try:
                    qty = int(float(qty_text)) if qty_text else 0
                except ValueError:
                    qty = 0

                price_text = ""
                цены = next((c for c in п if _ns(c.tag) == "Цены"), None)
                if цены is not None:
                    цена = next((c for c in цены if _ns(c.tag) == "Цена"), None)
                    if цена is not None:
                        price_text = _find(цена, "ЦенаЗаЕдиницу")
                try:
                    price = int(float(price_text)) if price_text else 0
                except ValueError:
                    price = 0

                if uid in products:
                    products[uid]["quantity"] = qty
                    if price:
                        products[uid]["price"] = price
                else:
                    # Предложение without a matching Товар — skip or create minimal
                    log.debug("CommerceML: предложение %s has no matching товар", uid_raw)

    result = [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in products.values()
        if p.get("size", 0) > 0  # skip items where size couldn't be determined
    ]
    skipped = len(products) - len(result)
    log.info("CommerceML parsed: %d items, %d skipped (no size)", len(result), skipped)
    return result
