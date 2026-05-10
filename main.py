
import os
import sqlite3
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

# Telegram
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK   = os.getenv("TELEGRAM_WEBHOOK_URL", "")   # https://yourdomain.com/tg/webhook

WHATSAPP_TOKEN     = os.getenv("WHATSAPP_TOKEN", "")          # Meta Business token
WHATSAPP_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY    = os.getenv("WHATSAPP_VERIFY_TOKEN", "mysecret")

INSTAGRAM_TOKEN    = os.getenv("INSTAGRAM_TOKEN", "")
INSTAGRAM_VERIFY   = os.getenv("INSTAGRAM_VERIFY_TOKEN", "mysecret")

DB_PATH = os.getenv("DB_PATH", "sneakers.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)

# ─── База данных ──────────────────────────────────────────────────────
def get_db():
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_placeholder() -> str:
    return "%s" if USE_POSTGRES else "?"

def fetch_all(query: str, params: list | tuple = ()) -> list[dict]:
    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def fetch_one_value(query: str, params: list | tuple = ()):
    conn = get_db()
    row = conn.execute(query, params).fetchone()
    conn.close()
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]

def search_sneakers(query: str) -> list[dict]:
    """Поиск по складу — по бренду, модели, расцветке, категории"""
    words = [w for w in query.lower().split() if len(w) > 2]
    if not words:
        return []
    
    ph = db_placeholder()
    conditions = " OR ".join(
        [f"(LOWER(brand) LIKE {ph} OR LOWER(model) LIKE {ph} OR LOWER(colorway) LIKE {ph} OR LOWER(category) LIKE {ph})"
         for _ in words]
    )
    params = []
    for w in words:
        params.extend([f"%{w}%"] * 4)
    
    return fetch_all(
        f"SELECT * FROM sneakers WHERE {conditions} ORDER BY brand, model, size",
        params
    )

def check_availability(brand: str = "", model: str = "", size: float = None) -> list[dict]:
    """Точная проверка наличия"""
    conds, params = ["1=1"], []
    ph = db_placeholder()
    if brand:
        conds.append(f"LOWER(brand) LIKE {ph}")
        params.append(f"%{brand.lower()}%")
    if model:
        conds.append(f"LOWER(model) LIKE {ph}")
        params.append(f"%{model.lower()}%")
    if size:
        conds.append(f"size = {ph}")
        params.append(size)
    
    return fetch_all(
        f"SELECT * FROM sneakers WHERE {' AND '.join(conds)} ORDER BY size",
        params
    )

def get_db_summary() -> str:
    """
    ОПТИМИЗАЦИЯ 1: Компактный текстовый формат вместо JSON.
    Экономия ~61% токенов на описании склада.
    Формат: Бренд Модель | цена | размеры | наличие
    """
    if USE_POSTGRES:
        query = """
            SELECT brand, model, MIN(price) as price, category,
                   STRING_AGG(DISTINCT CAST(size::INTEGER AS TEXT), ',') as sizes,
                   SUM(quantity) as total_qty
            FROM sneakers
            GROUP BY brand, model, category
            ORDER BY brand, model
        """
    else:
        query = (
            "SELECT brand, model, MIN(price) as price, category, "
            "GROUP_CONCAT(DISTINCT CAST(size AS INTEGER)) as sizes, "
            "SUM(quantity) as total_qty "
            "FROM sneakers GROUP BY brand, model ORDER BY brand, model"
        )
    rows = fetch_all(query)
    lines = []
    for r in rows:
        stock = "есть" if r["total_qty"] > 0 else "нет"
        lines.append(f"{r['brand']} {r['model']}|{r['price']}₸|р.{r['sizes']}|{stock}")
    return "\n".join(lines)

# ОПТИМИЗАЦИЯ 2: Системный промпт — только суть, без лишних слов.
# Каждый лишний токен в system prompt умножается на КАЖДЫЙ запрос.
SYSTEM_PROMPT = """Консультант магазина кроссовок. Отвечай по-русски, 2-3 предложения максимум.

СКЛАД (бренд модель|цена|размеры|наличие):
{db_summary}

Правила: проверяй наличие; если нет — предлагай 2 похожих из наличия; цены в ₸; не выдумывай."""

# Хранилище истории чатов (в продакшне — Redis или БД)
chat_sessions: dict[str, list] = {}

async def ask_ai(user_id: str, user_message: str) -> str:
    """
    Groq API — llama-3.1-8b-instant.
    Формат совместим с OpenAI: system идёт первым сообщением в messages[].
    История: последние 6 сообщений (3 пары) — экономия токенов.
    """
    if not user_message or not user_message.strip():
        return "Напишите, какую модель, размер или стиль кроссовок вы ищете."

    if user_id not in chat_sessions:
        chat_sessions[user_id] = []

    chat_sessions[user_id].append({"role": "user", "content": user_message})

    # Последние 6 сообщений истории
    history = chat_sessions[user_id][-6:]

    # Groq: system передаётся внутри messages как первый элемент
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(db_summary=get_db_summary())},
        *history,
    ]

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 250,
                    "temperature": 0.4,  # ниже дефолта — меньше "фантазий" про товары
                    "messages": messages,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error(f"Groq request failed: {e}")
            return fallback_reply(user_message)

    data = resp.json()

    if "error" in data:
        log.error(f"Groq error: {data['error']}")
        return fallback_reply(user_message)

    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "Ошибка, попробуйте позже.")

    chat_sessions[user_id].append({"role": "assistant", "content": reply})
    return reply

def fallback_reply(user_message: str) -> str:
    """Простой SQL-ответ, если ИИ временно недоступен."""
    items = search_sneakers(user_message)[:3]
    if not items:
        return "Сейчас не вижу точного совпадения на складе. Напишите бренд, модель или нужный размер — проверю по каталогу."

    lines = []
    for item in items:
        status = "есть" if item.get("quantity", 0) > 0 else "нет в наличии"
        lines.append(
            f"{item['brand']} {item['model']} {item.get('colorway') or ''}, "
            f"размер {item['size']}, {item['price']}₸ — {status}"
        )
    return "Нашёл по складу: " + "; ".join(lines)

# ─── Telegram ─────────────────────────────────────────────────────────
tg_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
tg_dp  = Dispatcher() if TELEGRAM_BOT_TOKEN else None

if tg_dp:
    @tg_dp.message(CommandStart())
    async def tg_start(msg: Message):
        await msg.answer(
            "Привет! Я SoleBot — ваш консультант по кроссовкам. 👟\n"
            "Спросите о любой модели — проверю наличие на складе!"
        )

    @tg_dp.message()
    async def tg_message(msg: Message):
        user_id = f"tg_{msg.from_user.id}"
        await msg.bot.send_chat_action(msg.chat.id, "typing")
        reply = await ask_ai(user_id, msg.text or "")
        await msg.answer(reply)

# ─── WhatsApp ─────────────────────────────────────────────────────────
async def send_whatsapp(to: str, text: str):
    """Отправить сообщение через WhatsApp Business API"""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v18.0/{WHATSAPP_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text}
            }
        )

# ─── Instagram ────────────────────────────────────────────────────────
async def send_instagram(recipient_id: str, text: str):
    """Отправить сообщение через Instagram Messenger API"""
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": INSTAGRAM_TOKEN},
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": text}
            }
        )

# ─── FastAPI app ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if tg_bot and TELEGRAM_WEBHOOK:
        try:
            webhook_url = TELEGRAM_WEBHOOK.rstrip("/") + "/tg/webhook"
            await tg_bot.set_webhook(webhook_url, drop_pending_updates=True)
            log.info(f"Telegram webhook установлен: {webhook_url}")
        except Exception as e:
            log.error(f"Ошибка установки webhook: {e}")
    yield
    if tg_bot:
        try:
            await tg_bot.session.close()
        except Exception:
            pass

app = FastAPI(title="SoleBot", lifespan=lifespan)

# ── Telegram webhook ──
@app.post("/tg/webhook")
async def telegram_webhook(request: Request):
    if not tg_bot:
        raise HTTPException(503, "Telegram не настроен")
    data = await request.json()
    update = types.Update.model_validate(data)
    await tg_dp.feed_update(tg_bot, update)
    return {"ok": True}

# ── WhatsApp webhook ──
@app.get("/wa/webhook")
async def whatsapp_verify(request: Request):
    """Meta требует верификацию webhook"""
    p = request.query_params
    if p.get("hub.verify_token") == WHATSAPP_VERIFY:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Неверный verify token")

@app.post("/wa/webhook")
async def whatsapp_message(request: Request):
    data = await request.json()
    try:
        entry    = data["entry"][0]
        change   = entry["changes"][0]["value"]
        msg      = change["messages"][0]
        phone    = msg["from"]
        text     = msg["text"]["body"]
        user_id  = f"wa_{phone}"
        
        reply = await ask_ai(user_id, text)
        await send_whatsapp(phone, reply)
    except (KeyError, IndexError):
        pass  # не все события содержат сообщения
    return {"status": "ok"}

# ── Instagram webhook ──
@app.get("/ig/webhook")
async def instagram_verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == INSTAGRAM_VERIFY:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Неверный verify token")

@app.post("/ig/webhook")
async def instagram_message(request: Request):
    data = await request.json()
    try:
        entry    = data["entry"][0]
        msg      = entry["messaging"][0]
        sender   = msg["sender"]["id"]
        text     = msg["message"]["text"]
        user_id  = f"ig_{sender}"
        
        reply = await ask_ai(user_id, text)
        await send_instagram(sender, reply)
    except (KeyError, IndexError):
        pass
    return {"status": "ok"}

# ── Web Chat API (для sneaker_bot.html) ──
@app.post("/api/chat")
async def web_chat(request: Request):
    data = await request.json()
    session_id = data.get("session_id", "web_anon")
    text       = data.get("message", "")
    user_id    = f"web_{session_id}"
    
    reply = await ask_ai(user_id, text)
    return {"reply": reply}

# ── Healthcheck ──
@app.get("/")
async def health():
    count = fetch_one_value("SELECT COUNT(*) FROM sneakers")
    return {
        "status": "ok",
        "database": "postgresql" if USE_POSTGRES else "sqlite",
        "sneakers_in_db": count,
        "channels": {
            "telegram":  bool(TELEGRAM_BOT_TOKEN),
            "whatsapp":  bool(WHATSAPP_TOKEN),
            "instagram": bool(INSTAGRAM_TOKEN),
            "web":       True,
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
