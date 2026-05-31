import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cache import close_redis
from routes.admin import router as admin_router
from routes.api import router as api_router
from schema import ensure_app_tables
from shops import ensure_default_shop_data
from telegram_bot import close_default_bot, close_shop_bots, setup_default_webhook, setup_shop_bots

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


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

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
