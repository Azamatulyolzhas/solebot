"""
CommerceML 2.x parser (used by 1С:Розница, 1С:УТ, 1С:Комплексная).
"""
import re
import logging
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)


def _ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find(el: ET.Element, *paths: str) -> str:
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


def _parse_size(text: str) -> float | None:
    m = re.search(r"\b(\d{2}(?:[.,]\d)?)\b", text)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_commerceml(xml_bytes: bytes) -> list[dict]:
    """
    Parse CommerceML XML into universal product dicts for import_products().

    Returns list of:
        {name, description, sku, category, price, quantity, attributes}
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    products: dict[str, dict] = {}

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

                attrs: dict = {}
                реквизиты = next((c for c in товар if _ns(c.tag) == "ЗначенияРеквизитов"), None)
                if реквизиты is not None:
                    for req in реквизиты:
                        req_name = _find(req, "Наименование").strip()
                        req_val = _find(req, "Значение").strip()
                        if not req_name or not req_val:
                            continue
                        key = req_name.lower()
                        if "размер" in key or key == "size":
                            size_val = _parse_size(req_val)
                            if size_val is not None:
                                attrs["size"] = size_val
                        elif "цвет" in key or key == "color":
                            attrs["color"] = req_val
                        else:
                            attrs[req_name] = req_val

                if "size" not in attrs and article:
                    size_val = _parse_size(article)
                    if size_val is not None:
                        attrs["size"] = size_val
                if "size" not in attrs and len(name) > 6:
                    size_val = _parse_size(name[-6:])
                    if size_val is not None:
                        attrs["size"] = size_val

                products[uid] = {
                    "name": name,
                    "description": None,
                    "sku": article or None,
                    "category": None,
                    "quantity": 0,
                    "price": 0,
                    "attributes": attrs,
                }

    пакет = next((c for c in root if _ns(c.tag) == "ПакетПредложений"), None)
    if пакет is not None:
        предложения = next((c for c in пакет if _ns(c.tag) == "Предложения"), None)
        if предложения is not None:
            for п in предложения:
                if _ns(п.tag) != "Предложение":
                    continue
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

    result = [
        p for p in products.values()
        if p.get("price", 0) > 0 or p.get("quantity", 0) > 0
    ]
    log.info("CommerceML parsed: %d items", len(result))
    return result
