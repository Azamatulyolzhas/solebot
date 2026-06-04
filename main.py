import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from cache import close_redis
from routes.admin import router as admin_router
from routes.api import router as api_router
from routes.shop import router as shop_router
from routes.sync import router as sync_router
from schema import ensure_app_tables
from shops import ensure_default_shop_data
from telegram_bot import close_default_bot, close_shop_bots, setup_default_webhook, setup_shop_bots

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

ADMIN_DIR     = Path(__file__).parent / "admin"
STORE_DIR     = Path(__file__).parent / "store"
DASHBOARD_DIR = Path(__file__).parent / "dashboard"
LANDING_DIR   = Path(__file__).parent / "landing"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_app_tables()
        ensure_default_shop_data()
        log.info("Database app tables are ready")
    except Exception as e:
        log.error(f"Database app table setup failed: {e}")

    await setup_default_webhook()
    await setup_shop_bots()
    yield
    await close_shop_bots()
    await close_redis()
    await close_default_bot()


app = FastAPI(title="SoleBot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(admin_router)
app.include_router(shop_router)
app.include_router(sync_router)


@app.get("/", include_in_schema=False)
async def landing_page():
    index = LANDING_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return RedirectResponse(url="/shop", status_code=302)


@app.get("/dashboard", include_in_schema=False)
async def redirect_dashboard():
    return RedirectResponse(url="/shop", status_code=301)

if LANDING_DIR.exists():
    app.mount("/landing/static", StaticFiles(directory=str(LANDING_DIR)), name="landing_static")
app.mount("/admin/static", StaticFiles(directory=str(ADMIN_DIR)), name="admin_static")
if STORE_DIR.exists():
    app.mount("/store/static", StaticFiles(directory=str(STORE_DIR)), name="store_static")
if DASHBOARD_DIR.exists():
    app.mount("/dashboard/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="dashboard_static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
