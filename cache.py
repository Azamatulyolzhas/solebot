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
last_shown_products: dict[str, list] = {}
session_last_activity: dict[str, float] = {}
redis_client = None

SESSION_IDLE_SECONDS = 30 * 60


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


async def set_last_shown_products(user_id: str, products: list[dict]) -> None:
    payload = [
        {"id": p.get("id"), "name": p.get("name"), "sku": p.get("sku")}
        for p in (products or [])[:8]
        if p.get("id") is not None
    ]
    if not payload:
        return
    try:
        client = await get_redis()
        if client is None:
            last_shown_products[user_id] = payload
            return

        await client.set(
            f"shown:{user_id}",
            json.dumps(payload, ensure_ascii=False),
            ex=SESSION_TTL_SECONDS,
        )
    except Exception as e:
        log.error(f"Set last shown products failed: {e}")
        last_shown_products[user_id] = payload


async def get_last_shown_products(user_id: str) -> list[dict]:
    try:
        client = await get_redis()
        if client is None:
            return last_shown_products.get(user_id, [])

        raw = await client.get(f"shown:{user_id}")
        return json.loads(raw) if raw else []
    except Exception as e:
        log.error(f"Get last shown products failed: {e}")
        return last_shown_products.get(user_id, [])


async def _delete_redis_keys(user_id: str, *suffixes: str) -> None:
    client = await get_redis()
    if client is None:
        return
    try:
        await client.delete(*[f"{suffix}:{user_id}" for suffix in suffixes])
    except Exception:
        log.exception("Redis delete keys failed for %s", user_id)


async def clear_chat_context(user_id: str) -> None:
    """Reset dialog history, product context, and order FSM."""
    chat_sessions.pop(user_id, None)
    last_product_interest.pop(user_id, None)
    last_shown_products.pop(user_id, None)
    order_states.pop(user_id, None)
    session_last_activity.pop(user_id, None)
    await _delete_redis_keys(
        user_id, "session", "interest", "shown", "order", "activity",
    )


async def get_session_activity(user_id: str) -> float | None:
    try:
        client = await get_redis()
        if client is None:
            return session_last_activity.get(user_id)

        raw = await client.get(f"activity:{user_id}")
        return float(raw) if raw else None
    except Exception as e:
        log.error(f"Get session activity failed: {e}")
        return session_last_activity.get(user_id)


async def touch_session_activity(user_id: str) -> None:
    now = time.time()
    try:
        client = await get_redis()
        if client is None:
            session_last_activity[user_id] = now
            return

        await client.set(f"activity:{user_id}", str(now), ex=SESSION_TTL_SECONDS)
    except Exception as e:
        log.error(f"Touch session activity failed: {e}")
        session_last_activity[user_id] = now


async def ensure_session_fresh(user_id: str) -> bool:
    """Clear stale context after idle timeout. Returns True if cleared."""
    now = time.time()
    last = await get_session_activity(user_id)
    if last is not None and (now - last) > SESSION_IDLE_SECONDS:
        await clear_chat_context(user_id)
        await touch_session_activity(user_id)
        return True
    await touch_session_activity(user_id)
    return False


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


# ── Handoff state ──────────────────────────────────────────────────────────────

_handoff_states: dict[str, bool] = {}
_miss_counts: dict[str, int] = {}

HANDOFF_TTL = 2 * 60 * 60  # 2 часа
MISS_TTL = 30 * 60          # 30 минут


async def get_handoff_state(user_id: str) -> bool:
    try:
        client = await get_redis()
        if client is None:
            return _handoff_states.get(user_id, False)
        return bool(await client.exists(f"handoff:{user_id}"))
    except Exception as e:
        log.error(f"Get handoff state failed: {e}")
        return _handoff_states.get(user_id, False)


async def set_handoff_state(user_id: str) -> None:
    try:
        client = await get_redis()
        if client is None:
            _handoff_states[user_id] = True
            return
        await client.set(f"handoff:{user_id}", "1", ex=HANDOFF_TTL)
    except Exception as e:
        log.error(f"Set handoff state failed: {e}")
        _handoff_states[user_id] = True


async def clear_handoff_state(user_id: str) -> None:
    _handoff_states.pop(user_id, None)
    _miss_counts.pop(user_id, None)
    try:
        client = await get_redis()
        if client is None:
            return
        await client.delete(f"handoff:{user_id}", f"miss:{user_id}")
    except Exception as e:
        log.error(f"Clear handoff state failed: {e}")


async def inc_miss_count(user_id: str) -> int:
    try:
        client = await get_redis()
        if client is None:
            count = _miss_counts.get(user_id, 0) + 1
            _miss_counts[user_id] = count
            return count
        key = f"miss:{user_id}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, MISS_TTL)
        return count
    except Exception as e:
        log.error(f"Inc miss count failed: {e}")
        count = _miss_counts.get(user_id, 0) + 1
        _miss_counts[user_id] = count
        return count


async def clear_miss_count(user_id: str) -> None:
    _miss_counts.pop(user_id, None)
    try:
        client = await get_redis()
        if client is None:
            return
        await client.delete(f"miss:{user_id}")
    except Exception as e:
        log.error(f"Clear miss count failed: {e}")
