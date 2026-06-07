"""
Inventory sync routes.

Ways to keep product stock up-to-date:

1. POST /sync/stock        — universal REST endpoint (JSON)
2. POST /sync/moysklad     — МойСклад webhook (auto on each sale)
3. POST /sync/1c           — manual 1С CommerceML XML upload
4. GET|POST /sync/1c-exchange — 1С "Обмен с сайтом" auto-push protocol
"""
import logging
import tempfile
import os

from fastapi import APIRouter, Header, HTTPException, Request, UploadFile, File
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from moysklad import sync_moysklad_catalog
from parse_commerceml import parse_commerceml
from products import import_products
from shops import get_shop_by_sync_api_key, set_shop_data_source

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sync")


def _require_shop_by_key(api_key: str | None) -> dict:
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    shop = get_shop_by_sync_api_key(api_key)
    if not shop:
        raise HTTPException(401, "Invalid API key")
    return shop


class StockItem(BaseModel):
    name: str
    description: str = ""
    sku: str = ""
    category: str = ""
    quantity: int = 0
    price: int | None = None
    attributes: dict = Field(default_factory=dict)


class LegacyStockItem(BaseModel):
    """Backward-compatible sneaker format — mapped to universal products."""
    brand: str
    model: str
    size: float
    quantity: int
    price: int | None = None
    colorway: str = ""
    category: str = ""
    gender: str = ""


class StockUpdateRequest(BaseModel):
    items: list[dict] = []
    replace: bool = False


def _normalize_stock_item(raw: dict) -> dict:
    if raw.get("brand") and raw.get("model"):
        return _legacy_to_product(LegacyStockItem(**raw))
    return _item_to_product(StockItem(**raw))


def _item_to_product(item: StockItem) -> dict:
    return {
        "name": item.name.strip(),
        "description": item.description.strip() or None,
        "sku": item.sku.strip() or None,
        "category": item.category.strip() or None,
        "quantity": item.quantity,
        "price": item.price or 0,
        "attributes": item.attributes or {},
    }


def _legacy_to_product(item: LegacyStockItem) -> dict:
    attrs = {}
    if item.size:
        attrs["size"] = item.size
    if item.colorway:
        attrs["colorway"] = item.colorway
    if item.gender:
        attrs["gender"] = item.gender
    attrs["brand"] = item.brand
    attrs["model"] = item.model
    return {
        "name": f"{item.brand} {item.model}".strip(),
        "description": None,
        "sku": None,
        "category": item.category.strip() or None,
        "quantity": item.quantity,
        "price": item.price or 0,
        "attributes": attrs,
    }


@router.post("/stock")
async def sync_stock(
    body: StockUpdateRequest,
    x_api_key: str | None = Header(default=None),
):
    """
    Universal inventory sync.
    Send current stock as JSON items with name, sku, price, quantity, attributes.

    Legacy format (brand/model/size) also accepted via legacy_items field.
    """
    shop = _require_shop_by_key(x_api_key)
    if not body.items:
        raise HTTPException(400, "items required")

    products = [_normalize_stock_item(item) for item in body.items]
    imported = import_products(products, replace=body.replace, shop_id=shop["id"])
    set_shop_data_source(shop["id"], "api")
    log.info("sync/stock: shop=%s imported=%s replace=%s", shop["id"], imported, body.replace)
    return {"ok": True, "shop_id": shop["id"], "imported": imported, "replace": body.replace}


@router.post("/moysklad")
async def moysklad_webhook(
    request: Request,
    x_api_key: str | None = Header(default=None),
):
    shop = _require_shop_by_key(x_api_key)

    payload = await request.json()
    events = payload.get("events", [])
    log.info("МойСклад webhook: shop=%s events=%d", shop["id"], len(events))

    if not events:
        return {"ok": True, "message": "no events"}

    ms_token = shop.get("moysklad_token")
    if not ms_token:
        raise HTTPException(400, "МойСклад token not configured. Set it in Bot Settings.")

    result = await sync_moysklad_catalog(ms_token, shop["id"])
    set_shop_data_source(shop["id"], "moysklad")
    if result.get("error"):
        raise HTTPException(502, result["error"])
    log.info(
        "МойСклад webhook sync: shop=%s imported=%s ms_rows=%s in_stock=%s",
        shop["id"], result["imported"], result["ms_rows"], result.get("in_stock"),
    )
    return {"ok": True, **result}


@router.post("/1c")
async def sync_1c(
    file: UploadFile = File(...),
    replace: bool = False,
    x_api_key: str | None = Header(default=None),
):
    shop = _require_shop_by_key(x_api_key)

    if not file.filename or not file.filename.lower().endswith(".xml"):
        raise HTTPException(400, "Please upload a .xml file")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 50 MB)")

    try:
        items = parse_commerceml(content)
    except ValueError as exc:
        raise HTTPException(422, f"XML parse error: {exc}") from exc

    if not items:
        return {"ok": True, "imported": 0, "message": "No items found in the XML"}

    imported = import_products(items, replace=replace, shop_id=shop["id"])
    set_shop_data_source(shop["id"], "1c")
    log.info("sync/1c: shop=%s parsed=%d imported=%d replace=%s",
             shop["id"], len(items), imported, replace)
    return {
        "ok":       True,
        "shop_id":  shop["id"],
        "parsed":   len(items),
        "imported": imported,
        "replace":  replace,
    }


_TMP_PREFIX = "salebot_1c_"


def _get_1c_shop(request: Request) -> dict:
    import base64
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        raise HTTPException(
            status_code=401,
            detail="failure\nНужна авторизация (Basic Auth)",
            headers={"WWW-Authenticate": "Basic realm=\"1C Exchange\""},
        )
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        _, password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(401, "failure\nНеверный формат авторизации")

    shop = get_shop_by_sync_api_key(password.strip())
    if not shop:
        raise HTTPException(401, "failure\nНеверный API ключ")
    return shop


def _tmp_path(shop_id: int, filename: str) -> str:
    safe_name = os.path.basename(filename).replace("/", "_").replace("\\", "_")
    return os.path.join(tempfile.gettempdir(), f"{_TMP_PREFIX}{shop_id}_{safe_name}")


@router.get("/1c-exchange", response_class=PlainTextResponse)
async def exchange_1c_get(request: Request, type: str = "catalog", mode: str = "", filename: str = ""):
    shop = _get_1c_shop(request)

    if mode == "checkauth":
        api_key = shop.get("sync_api_key", "")
        log.info("1c-exchange checkauth: shop=%s", shop["id"])
        return f"success\nsid\n{api_key}"

    if mode == "init":
        return "zip=no\nfile_limit=52428800"

    if mode == "import":
        if not filename:
            return "failure\nFilename not specified"
        path = _tmp_path(shop["id"], filename)
        if not os.path.exists(path):
            return f"failure\nFile {filename} not found. Upload it first."
        try:
            with open(path, "rb") as f:
                xml_bytes = f.read()
            items = parse_commerceml(xml_bytes)
            if not items:
                os.unlink(path)
                return "success\n0 items"
            imported = import_products(items, replace=False, shop_id=shop["id"])
            set_shop_data_source(shop["id"], "1c")
            os.unlink(path)
            log.info("1c-exchange import: shop=%s parsed=%d imported=%d", shop["id"], len(items), imported)
            return f"success\n{imported} positions imported"
        except Exception as exc:
            log.error("1c-exchange import error: %s", exc)
            return f"failure\n{exc}"

    return "failure\nUnknown mode"


@router.post("/1c-exchange", response_class=PlainTextResponse)
async def exchange_1c_post(request: Request, type: str = "catalog", mode: str = "", filename: str = ""):
    shop = _get_1c_shop(request)

    if mode == "file":
        if not filename:
            return "failure\nFilename not specified"
        path = _tmp_path(shop["id"], filename)
        try:
            body = await request.body()
            with open(path, "wb") as f:
                f.write(body)
            log.info("1c-exchange file upload: shop=%s file=%s size=%d", shop["id"], filename, len(body))
            return "success"
        except Exception as exc:
            log.error("1c-exchange file write error: %s", exc)
            return f"failure\n{exc}"

    return "failure\nUnknown mode"


