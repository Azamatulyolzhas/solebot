"""
Inventory sync routes.

Ways to keep product stock up-to-date:

1. POST /sync/stock        — universal REST endpoint (JSON)
2. POST /sync/moysklad     — МойСклад webhook (auto on each sale)
3. POST /sync/1c           — manual 1С CommerceML XML upload
4. GET|POST /sync/1c-exchange — 1С "Обмен с сайтом" auto-push protocol
   Compatible with 1С:Розница, 1С:УТ, 1С:Комплексная.
   Configure once in 1С → data is pushed automatically on schedule.
"""
import logging
import tempfile
import os

from fastapi import APIRouter, Header, HTTPException, Request, UploadFile, File
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from moysklad import get_stock, parse_stock_rows
from parse_commerceml import parse_commerceml
from products import import_products, update_product, list_products
from shops import get_shop_by_sync_api_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sync")


# ── Auth helper ────────────────────────────────────────────────────────────────

def _require_shop_by_key(api_key: str | None) -> dict:
    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    shop = get_shop_by_sync_api_key(api_key)
    if not shop:
        raise HTTPException(401, "Invalid API key")
    return shop


# ── Universal stock update ─────────────────────────────────────────────────────

class StockItem(BaseModel):
    brand: str
    model: str
    size: float
    quantity: int
    price: int | None = None
    colorway: str = ""
    category: str = "lifestyle"
    gender: str = "unisex"


class StockUpdateRequest(BaseModel):
    items: list[StockItem]
    replace: bool = False   # if True — replaces entire catalog; if False — upserts


@router.post("/stock")
async def sync_stock(
    body: StockUpdateRequest,
    x_api_key: str | None = Header(default=None),
):
    """
    Universal inventory sync.
    Send your current stock, we update the DB.

    Example (with curl):
        curl -X POST https://your-app.railway.app/sync/stock \\
          -H "X-API-Key: sk_xxx" \\
          -H "Content-Type: application/json" \\
          -d '{"items": [{"brand":"Nike","model":"Air Force 1","size":42,"quantity":3,"price":45000}]}'
    """
    shop = _require_shop_by_key(x_api_key)
    products = [
        {
            "brand":    item.brand,
            "model":    item.model,
            "colorway": item.colorway,
            "size":     item.size,
            "quantity": item.quantity,
            "price":    item.price or 0,
            "category": item.category,
            "gender":   item.gender,
        }
        for item in body.items
    ]
    imported = import_products(products, replace=body.replace, shop_id=shop["id"])
    log.info("sync/stock: shop=%s imported=%s replace=%s", shop["id"], imported, body.replace)
    return {"ok": True, "shop_id": shop["id"], "imported": imported, "replace": body.replace}


# ── МойСклад webhook ───────────────────────────────────────────────────────────

@router.post("/moysklad")
async def moysklad_webhook(
    request: Request,
    x_api_key: str | None = Header(default=None),
):
    """
    МойСклад webhook endpoint.

    How to set up in МойСклад:
    1. Settings → Webhooks → Create webhook
    2. URL: https://your-app.railway.app/sync/moysklad
    3. Event types: demand (Отгрузка), retaildemand (Розничная продажа)
    4. Add header: X-API-Key: <your shop API key>

    On each sale — we pull full stock from МойСклад API and sync.
    """
    shop = _require_shop_by_key(x_api_key)

    payload = await request.json()
    events = payload.get("events", [])
    log.info("МойСклад webhook: shop=%s events=%d", shop["id"], len(events))

    if not events:
        return {"ok": True, "message": "no events"}

    ms_token = shop.get("moysklad_token")
    if not ms_token:
        raise HTTPException(400, "МойСклад token not configured. Set it in Bot Settings.")

    # Pull full stock from МойСклад and sync
    rows = await get_stock(ms_token)
    if not rows:
        return {"ok": True, "message": "no stock data returned from МойСклад"}

    parsed = parse_stock_rows(rows)
    synced = _sync_quantities(parsed, shop["id"])
    log.info("МойСклад sync: shop=%s updated=%d", shop["id"], synced)
    return {"ok": True, "synced": synced}


# ── 1С CommerceML XML upload ───────────────────────────────────────────────────

@router.post("/1c")
async def sync_1c(
    file: UploadFile = File(...),
    replace: bool = False,
    x_api_key: str | None = Header(default=None),
):
    """
    Upload a CommerceML XML file exported from 1С.

    How to export from 1С:Розница / 1С:УТ:
      Файл → Обмен данными → Выгрузить в CommerceML

    Curl example:
        curl -X POST https://your-app.railway.app/sync/1c \\
          -H "X-API-Key: sk_xxx" \\
          -F "file=@catalog.xml" \\
          -F "replace=false"
    """
    shop = _require_shop_by_key(x_api_key)

    if not file.filename or not file.filename.lower().endswith(".xml"):
        raise HTTPException(400, "Please upload a .xml file")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50 MB limit
        raise HTTPException(413, "File too large (max 50 MB)")

    try:
        items = parse_commerceml(content)
    except ValueError as exc:
        raise HTTPException(422, f"XML parse error: {exc}") from exc

    if not items:
        return {"ok": True, "imported": 0, "message": "No items with sizes found in the XML"}

    products = [
        {
            "brand":    it["brand"],
            "model":    it["model"],
            "colorway": it.get("colorway", ""),
            "size":     it["size"],
            "quantity": it["quantity"],
            "price":    it.get("price", 0),
            "category": it.get("category", "lifestyle"),
            "gender":   it.get("gender", "unisex"),
        }
        for it in items
    ]

    imported = import_products(products, replace=replace, shop_id=shop["id"])
    log.info("sync/1c: shop=%s parsed=%d imported=%d replace=%s",
             shop["id"], len(items), imported, replace)
    return {
        "ok":       True,
        "shop_id":  shop["id"],
        "parsed":   len(items),
        "imported": imported,
        "replace":  replace,
    }


# ── 1С "Обмен с сайтом" auto-push protocol ────────────────────────────────────
#
# How to set up in 1С:Розница / 1С:УТ (one-time setup):
#   Administration → Exchange with site → Create new exchange
#   URL:      https://your-app.railway.app/sync/1c-exchange
#   Login:    shop   (any value)
#   Password: <your Sync API Key from Bot Settings>
#   Schedule: every 15 min / 1 hour / etc.
#
# Protocol flow (1С initiates):
#   GET  ?type=catalog&mode=checkauth   → success\nsid\n<api_key>
#   GET  ?type=catalog&mode=init        → zip=no\nfile_limit=52428800
#   POST ?type=catalog&mode=file&filename=import.xml  (XML body)  → success
#   GET  ?type=catalog&mode=import&filename=import.xml → success\n<N> items imported
#
# Auth: HTTP Basic, password = sync_api_key
# Temp files: stored in OS temp dir, keyed by shop_id

_TMP_PREFIX = "solebot_1c_"


def _get_1c_shop(request: Request) -> dict:
    """Authenticate using HTTP Basic Auth; password = sync_api_key."""
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
    """1С Exchange Protocol — GET handler."""
    shop = _get_1c_shop(request)

    if mode == "checkauth":
        # 1С checks credentials. Response: success\ncookiename\ncookievalue
        api_key = shop.get("sync_api_key", "")
        log.info("1c-exchange checkauth: shop=%s", shop["id"])
        return f"success\nsid\n{api_key}"

    if mode == "init":
        # 1С asks for upload limits
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
                return "success\n0 items (no positions with sizes found)"
            products = [
                {k: v for k, v in it.items() if not k.startswith("_")}
                for it in items
            ]
            imported = import_products(products, replace=False, shop_id=shop["id"])
            os.unlink(path)
            log.info("1c-exchange import: shop=%s parsed=%d imported=%d", shop["id"], len(items), imported)
            return f"success\n{imported} positions imported"
        except Exception as exc:
            log.error("1c-exchange import error: %s", exc)
            return f"failure\n{exc}"

    return "failure\nUnknown mode"


@router.post("/1c-exchange", response_class=PlainTextResponse)
async def exchange_1c_post(request: Request, type: str = "catalog", mode: str = "", filename: str = ""):
    """1С Exchange Protocol — POST handler (file upload)."""
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


def _sync_quantities(ms_rows: list[dict], shop_id: int) -> int:
    """
    Match МойСклад stock rows to our DB by article/name and update quantities.
    Returns count of updated rows.
    """
    db_products = list_products(limit=10000, shop_id=shop_id)
    updated = 0
    for ms in ms_rows:
        article = (ms.get("article") or "").lower().strip()
        name    = (ms.get("name") or "").lower().strip()
        qty     = ms["quantity"]
        price   = ms.get("price_rub")

        for p in db_products:
            # Match by: article == model, or name contains brand+model
            p_model = (p.get("model") or "").lower().strip()
            p_brand = (p.get("brand") or "").lower().strip()
            match = (
                (article and article == p_model) or
                (name and p_brand in name and p_model in name)
            )
            if match:
                update_product(p["id"], price=price, quantity=qty, shop_id=shop_id)
                updated += 1
                break
    return updated
