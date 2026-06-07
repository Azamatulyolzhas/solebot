import json
import logging
import time

from config import (
    RATE_LIMIT_MESSAGES,
    RATE_LIMIT_WINDOW_SECONDS,
    REDIS_URL,
    SESSION_TTL_SECONDS,
)

try:
    import redis.asyncio as redis
except ImportError:
    redis = None


log = logging.getLogger(__name__)

chat_sessions: dict[str, list] = {}
memory_rate_limits: dict[str, tuple[int, int]] = {}
order_states: dict[str, dict] = {}
last_product_interest: dict[str, str] = {}
redis_client = None


async def get_redis():
    global redis_client
    if not REDIS_URL or redis is None:
        return None
    if redis_client is None:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


async def close_redis() -> None:
    global redis_client
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None


async def get_redis_status() -> dict:
    if not REDIS_URL:
        return {"redis": "disabled", "redis_ok": False}
    if redis is None:
        return {"redis": "missing_dependency", "redis_ok": False}
    try:
        client = await get_redis()
        await client.ping()
        return {"redis": "enabled", "redis_ok": True}
    except Exception as e:
        log.exception("Redis healthcheck failed")
        return {"redis": "enabled", "redis_ok": False, "redis_error": type(e).__name__}


async def check_rate_limit(user_id: str) -> tuple[bool, int]:
    client = await get_redis()
    if client is None:
        return check_memory_rate_limit(user_id)

    key = f"rate:{user_id}:{int(time.time() // RATE_LIMIT_WINDOW_SECONDS)}"
    try:
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, RATE_LIMIT_WINDOW_SECONDS + 5)
        remaining = max(0, RATE_LIMIT_MESSAGES - current)
        return current <= RATE_LIMIT_MESSAGES, remaining
    except Exception:
        log.exception("Redis rate limit failed")
        return check_memory_rate_limit(user_id)


def check_memory_rate_limit(user_id: str) -> tuple[bool, int]:
    bucket = int(time.time() // RATE_LIMIT_WINDOW_SECONDS)
    key = f"{user_id}:{bucket}"
    count, _ = memory_rate_limits.get(key, (0, bucket))
    count += 1
    memory_rate_limits[key] = (count, bucket)
    remaining = max(0, RATE_LIMIT_MESSAGES - count)
    return count <= RATE_LIMIT_MESSAGES, remaining


async def save_session_message(user_id: str, role: str, content: str) -> None:
    message = json.dumps({"role": role, "content": content}, ensure_ascii=False)
    client = await get_redis()
    if client is None:
        chat_sessions.setdefault(user_id, []).append({"role": role, "content": content})
        chat_sessions[user_id] = chat_sessions[user_id][-6:]
        return

    key = f"session:{user_id}"
    try:
        await client.rpush(key, message)
        await client.ltrim(key, -6, -1)
        await client.expire(key, SESSION_TTL_SECONDS)
    except Exception:
        log.exception("Redis session write failed")
        chat_sessions.setdefault(user_id, []).append({"role": role, "content": content})
        chat_sessions[user_id] = chat_sessions[user_id][-6:]


async def load_session_history(user_id: str) -> list[dict]:
    client = await get_redis()
    if client is None:
        return chat_sessions.get(user_id, [])[-6:]

    key = f"session:{user_id}"
    try:
        raw_messages = await client.lrange(key, -6, -1)
        return [json.loads(item) for item in raw_messages]
    except Exception:
        log.exception("Redis session read failed")
        return chat_sessions.get(user_id, [])[-6:]


async def get_order_state(user_id: str) -> dict | None:
    try:
        client = await get_redis()
        if client is None:
            return order_states.get(user_id)

        raw_state = await client.get(f"order:{user_id}")
        return json.loads(raw_state) if raw_state else None
    except Exception as e:
        log.error(f"Get order state failed: {e}")
        return order_states.get(user_id)


async def set_order_state(user_id: str, state: dict) -> None:
    try:
        client = await get_redis()
        if client is None:
            order_states[user_id] = state
            return

        await client.set(f"order:{user_id}", json.dumps(state, ensure_ascii=False), ex=SESSION_TTL_SECONDS)
    except Exception as e:
        log.error(f"Set order state failed: {e}")
        order_states[user_id] = state


async def get_last_product_interest(user_id: str) -> str | None:
    try:
        client = await get_redis()
        if client is None:
            return last_product_interest.get(user_id)

        raw = await client.get(f"interest:{user_id}")
        return raw if raw else None
    except Exception as e:
        log.error(f"Get last product interest failed: {e}")
        return last_product_interest.get(user_id)


async def set_last_product_interest(user_id: str, product: str) -> None:
    product = (product or "").strip()
    if not product:
        return
    try:
        client = await get_redis()
        if client is None:
            last_product_interest[user_id] = product
            return

        await client.set(f"interest:{user_id}", product, ex=SESSION_TTL_SECONDS)
    except Exception as e:
        log.error(f"Set last product interest failed: {e}")
        last_product_interest[user_id] = product


async def clear_order_state(user_id: str) -> None:
    try:
        client = await get_redis()
        if client is None:
            order_states.pop(user_id, None)
            return

        await client.delete(f"order:{user_id}")
    except Exception as e:
        log.error(f"Clear order state failed: {e}")
        order_states.pop(user_id, None)
