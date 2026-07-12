"""Shop owner portal API.

All endpoints are under /shop prefix.
Authentication: Bearer JWT token obtained via POST /shop/login.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

from admin_service import count_shop_messages, count_shop_rows, list_recent_messages
from db import db_placeholder, fetch_all
from auth import create_shop_token, decode_shop_claims, hash_password, verify_password
from conversations import log_analytics_event
from orders import ORDER_STATUSES, list_orders, update_order_status
from products import (
    import_products,
    list_products,
    products_to_csv,
    update_product,
    validate_product_csv,
)
from email_service import send_email_verification, send_password_reset, send_shop_welcome
from shops import (
    clear_moysklad_token,
    clear_shop_tg_token,
    consume_email_verification_token,
    consume_reset_token,
    create_active_shop_with_trial,
    create_email_verification_token,
    create_password_reset_token,
    generate_sync_api_key,
    get_shop_by_email,
    get_shop_by_id,
    get_shop_subscription_detail,
    get_valid_email_verification_token,
    get_valid_reset_token,
    mark_email_verified,
    resolve_data_source,
    save_moysklad_token,
    save_shop_tg_token,
    set_shop_data_source,
    set_shop_owner_password,
    update_shop_settings,
)

import time as _time
from collections import defaultdict as _defaultdict

log = logging.getLogger(__name__)

DASHBOARD_INDEX = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

router = APIRouter(prefix="/shop")
_bearer = HTTPBearer(auto_error=False)

_login_attempts: dict[str, list[float]] = _defaultdict(list)
_LOGIN_MAX = 10
_LOGIN_WINDOW = 60

_register_attempts: dict[str, list[float]] = _defaultdict(list)
_REGISTER_MAX = 5
_REGISTER_WINDOW = 600

_sandbox_attempts: dict[int, list[float]] = _defaultdict(list)
_SANDBOX_MAX = 30
_SANDBOX_WINDOW = 3600


def _real_client_ip(request: Request) -> str:
    """Behind a reverse proxy (Railway), request.client.host is the proxy IP.
    Trust the leftmost X-Forwarded-For so the rate limit keys on the real client."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _check_login_rate(ip: str) -> None:
    now = _time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < _LOGIN_WINDOW]
    attempts.append(now)
    _login_attempts[ip] = attempts
    if len(attempts) > _LOGIN_MAX:
        raise HTTPException(429, "Слишком много попыток входа. Подождите минуту.")


def _check_register_rate(ip: str) -> None:
    now = _time.time()
    attempts = [t for t in _register_attempts[ip] if now - t < _REGISTER_WINDOW]
    attempts.append(now)
    _register_attempts[ip] = attempts
    if len(attempts) > _REGISTER_MAX:
        raise HTTPException(429, "Слишком много регистраций с этого адреса. Попробуйте позже.")


def _check_sandbox_rate(shop_id: int) -> None:
    now = _time.time()
    attempts = [t for t in _sandbox_attempts[shop_id] if now - t < _SANDBOX_WINDOW]
    attempts.append(now)
    _sandbox_attempts[shop_id] = attempts
    if len(attempts) > _SANDBOX_MAX:
        raise HTTPException(
            429,
            f"Лимит на тестирование — {_SANDBOX_MAX} сообщений в час. Подождите и продолжите.",
        )


# ── Auth dependency ────────────────────────────────────────────────────────────

def get_current_shop(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    token = credentials.credentials if credentials else None
    claims = decode_shop_claims(token) if token else None
    if not claims:
        raise HTTPException(401, "Требуется авторизация")
    shop_id, token_version = claims
    shop = get_shop_by_id(shop_id)
    if not shop or shop.get("status") != "active":
        raise HTTPException(403, "Магазин не найден или заблокирован")
    # Password change bumps shops.token_version — every token minted before
    # the bump carries a stale "tv" and dies here.
    if int(shop.get("token_version") or 0) != token_version:
        raise HTTPException(401, "Сессия недействительна — войдите заново")
    return shop


def require_verified_shop(shop: dict = Depends(get_current_shop)) -> dict:
    """Wraps get_current_shop. Read-only endpoints can stay on get_current_shop;
    anything that hands out a sync key, connects an external account, or burns
    quota must go through this so unverified emails can't be weaponised.

    When config.EMAIL_VERIFICATION_ENABLED is False (default), this is a no-op
    — every authenticated shop passes through. Re-enable by setting
    EMAIL_VERIFICATION_ENABLED=true in env once Resend domain is verified."""
    from config import EMAIL_VERIFICATION_ENABLED

    if EMAIL_VERIFICATION_ENABLED and not shop.get("email_verified"):
        raise HTTPException(
            403,
            "Подтвердите email — мы отправили письмо на " + (shop.get("owner_email") or "ваш адрес"),
        )
    return shop


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    shop_name: str
    email: EmailStr
    password: str
    accepted_terms: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SettingsUpdate(BaseModel):
    name: str | None = None
    groq_system_prompt: str | None = None
    groq_api_key: str | None = None
    clear_groq_api_key: bool = False
    bot_role: str | None = None
    business_type: str | None = None
    website_url: str | None = None
    owner_telegram_chat_id: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class ProductPatch(BaseModel):
    price: int | None = None
    quantity: int | None = None


class OrderPatch(BaseModel):
    status: str


class SandboxMessage(BaseModel):
    role: str
    content: str


class SandboxChatRequest(BaseModel):
    message: str
    history: list[SandboxMessage] = []


# ── Public routes ──────────────────────────────────────────────────────────────

@router.post("/register")
async def shop_register(body: RegisterRequest, request: Request):
    """Public self-service registration. Auto-provisions an active shop + 14-day trial and returns a JWT
    so the dashboard logs in immediately (no manual approval step)."""
    try:
        _check_register_rate(_real_client_ip(request))
        if len(body.password) < 8:
            raise HTTPException(400, "Пароль должен быть не менее 8 символов")
        shop_name = body.shop_name.strip()
        if len(shop_name) < 2:
            raise HTTPException(400, "Введите название магазина")
        if not body.accepted_terms:
            raise HTTPException(400, "Необходимо принять условия оферты и политику конфиденциальности")
        existing = get_shop_by_email(body.email)
        if existing:
            raise HTTPException(409, "Этот email уже зарегистрирован")
        pwd_hash = hash_password(body.password)
        shop_id = create_active_shop_with_trial(shop_name, body.email, pwd_hash)
        if not shop_id:
            raise HTTPException(500, "Не удалось создать магазин, попробуйте позже")
        subscription = get_shop_subscription_detail(shop_id)
        send_shop_welcome(shop_name, body.email, subscription)
        # Verification email only when the gate is enabled — no point spamming
        # owners with a "confirm email" link they're not required to click.
        from config import EMAIL_VERIFICATION_ENABLED
        if EMAIL_VERIFICATION_ENABLED:
            verify_token = create_email_verification_token(shop_id)
            if verify_token:
                try:
                    send_email_verification(body.email, verify_token)
                except Exception:
                    log.exception("send_email_verification failed shop=%s", shop_id)
        try:
            log_analytics_event("dashboard", "signup_completed", {"email": body.email}, shop_id=shop_id)
        except Exception:
            log.exception("signup_completed event failed shop=%s", shop_id)
        token = create_shop_token(shop_id)
        return {
            "ok": True,
            "token": token,
            "shop_id": shop_id,
            "name": shop_name,
            "subscription": subscription,
        }
    except HTTPException:
        raise
    except Exception:
        log.exception("Register failed")
        raise HTTPException(500, "Ошибка сервера, попробуйте позже") from None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


@router.post("/forgot-password")
async def shop_forgot_password(body: ForgotPasswordRequest):
    """Send password reset email. Always returns 200 to avoid email enumeration."""
    shop = get_shop_by_email(body.email)
    if shop:
        token = create_password_reset_token(shop["id"])
        if token:
            send_password_reset(body.email, token)
    return {"ok": True, "message": "Если аккаунт существует — письмо отправлено"}


class VerifyEmailRequest(BaseModel):
    token: str


@router.post("/verify-email")
async def shop_verify_email(body: VerifyEmailRequest):
    """Public endpoint — the link in the verification email POSTs here."""
    token_row = get_valid_email_verification_token(body.token)
    if not token_row:
        raise HTTPException(400, "Ссылка недействительна или истекла")
    mark_email_verified(token_row["shop_id"])
    consume_email_verification_token(token_row["id"])
    return {"ok": True}


@router.post("/resend-verification")
async def shop_resend_verification(shop: dict = Depends(get_current_shop)):
    """Authenticated — for the 'Send again' button in the dashboard banner.
    Only useful for unverified shops; no-op (but returns ok) for already-verified."""
    if shop.get("email_verified"):
        return {"ok": True, "already_verified": True}
    token = create_email_verification_token(shop["id"])
    if token and shop.get("owner_email"):
        try:
            send_email_verification(shop["owner_email"], token)
        except Exception:
            log.exception("Resend verification email failed shop=%s", shop["id"])
    return {"ok": True}


@router.get("/verify-email", response_class=HTMLResponse, include_in_schema=False)
async def shop_verify_email_landing(token: str = ""):
    """Tiny inline page that POSTs the token then bounces to /shop on success.
    Saves us from having to add a separate frontend route."""
    safe_token = (token or "").replace('"', "").replace("\\", "")
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Подтверждение email</title>
<style>body{{font-family:sans-serif;padding:48px;text-align:center;color:#0d0d0d}}
.msg{{font-size:18px;margin-top:24px}}</style></head><body>
<h1>Подтверждение email</h1><div id="msg" class="msg">Проверяем ссылку…</div>
<script>
fetch("/shop/verify-email", {{
  method: "POST",
  headers: {{ "Content-Type": "application/json" }},
  body: JSON.stringify({{ token: "{safe_token}" }})
}}).then(async r => {{
  const m = document.getElementById("msg");
  if (r.ok) {{
    m.textContent = "Email подтверждён. Перенаправляем в кабинет…";
    setTimeout(() => location.href = "/shop", 1500);
  }} else {{
    const d = await r.json().catch(() => ({{}}));
    m.textContent = d.detail || "Ссылка недействительна или истекла";
  }}
}}).catch(() => {{
  document.getElementById("msg").textContent = "Сетевая ошибка. Попробуйте ещё раз.";
}});
</script></body></html>""")


@router.post("/reset-password")
async def shop_reset_password(body: ResetPasswordRequest):
    """Validate reset token and set new password."""
    if len(body.password) < 8:
        raise HTTPException(400, "Пароль должен быть не менее 8 символов")
    token_row = get_valid_reset_token(body.token)
    if not token_row:
        raise HTTPException(400, "Ссылка недействительна или истекла")
    pwd_hash = hash_password(body.password)
    set_shop_owner_password(token_row["shop_id"], pwd_hash)
    consume_reset_token(token_row["id"])
    return {"ok": True, "message": "Пароль успешно изменён"}


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
async def dashboard_page():
    if not DASHBOARD_INDEX.exists():
        return HTMLResponse("<h1>Dashboard coming soon</h1>")
    from asset_versioning import add_cache_bust
    html = add_cache_bust(DASHBOARD_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse(html, headers=_NO_CACHE)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_redirect():
    """Legacy redirect: /shop/dashboard → /shop."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/shop", status_code=301)


@router.post("/login")
async def shop_login(body: LoginRequest, request: Request):
    _check_login_rate(_real_client_ip(request))
    shop = get_shop_by_email(body.email)
    if not shop:
        raise HTTPException(401, "Неверный email или пароль")
    pwd_hash = shop.get("owner_password_hash") or ""
    if not pwd_hash or not verify_password(body.password, pwd_hash):
        raise HTTPException(401, "Неверный email или пароль")
    if shop.get("status") != "active":
        raise HTTPException(403, "Магазин заблокирован")
    token = create_shop_token(shop["id"], int(shop.get("token_version") or 0))
    return {"token": token, "shop_id": shop["id"], "name": shop["name"]}


# ── Protected routes ───────────────────────────────────────────────────────────

@router.get("/me")
async def shop_me(shop: dict = Depends(get_current_shop)):
    sub = get_shop_subscription_detail(shop["id"])
    return {
        "id": shop["id"],
        "name": shop["name"],
        "slug": shop["slug"],
        "status": shop.get("status", "active"),
        "owner_email": shop["owner_email"],
        "email_verified": bool(shop.get("email_verified")),
        "groq_system_prompt": shop.get("groq_system_prompt") or "",
        "bot_role": shop.get("bot_role") or "",
        "business_type": shop.get("business_type") or "",
        "website_url": shop.get("website_url") or "",
        "data_source": resolve_data_source(shop),
        "has_tg_bot": bool(shop.get("tg_token")),
        "owner_telegram_chat_id": shop.get("owner_telegram_chat_id") or "",
        "has_order_notify": bool((shop.get("owner_telegram_chat_id") or "").strip()),
        "has_moysklad": bool(shop.get("moysklad_token")),
        "has_own_groq_key": bool((shop.get("groq_api_key") or "").strip()),
        "sync_api_key": shop.get("sync_api_key") or None,
        "subscription": sub,
    }


@router.get("/analytics/overview")
async def analytics_overview(shop: dict = Depends(get_current_shop)):
    from db import fetch_all, db_placeholder
    import config as _cfg
    sid = shop["id"]
    ph = db_placeholder()
    try:
        if _cfg.USE_POSTGRES:
            daily = fetch_all(f"""
                SELECT DATE(m.created_at) AS day, COUNT(*) AS cnt
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE c.shop_id = {ph}
                  AND m.role = 'user'
                  AND m.created_at >= NOW() - INTERVAL '14 days'
                GROUP BY day ORDER BY day
            """, (sid,))
        else:
            daily = fetch_all(f"""
                SELECT DATE(m.created_at) AS day, COUNT(*) AS cnt
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE c.shop_id = {ph}
                  AND m.role = 'user'
                  AND m.created_at >= datetime('now','-14 days')
                GROUP BY day ORDER BY day
            """, (sid,))

        top_categories = fetch_all(f"""
            SELECT COALESCE(NULLIF(category, ''), 'Без категории') AS category, COUNT(*) AS cnt
            FROM products WHERE shop_id = {ph} AND quantity > 0
            GROUP BY category ORDER BY cnt DESC LIMIT 8
        """, (sid,))

        uniq = fetch_all(f"""
            SELECT COUNT(DISTINCT external_user_id) AS cnt
            FROM conversations WHERE shop_id = {ph}
        """, (sid,))

        order_stats = fetch_all(f"""
            SELECT status, COUNT(*) AS cnt
            FROM orders WHERE shop_id = {ph}
            GROUP BY status
        """, (sid,))

        top_models = fetch_all(f"""
            SELECT name, SUM(quantity) AS stock
            FROM products WHERE shop_id = {ph}
            GROUP BY name ORDER BY stock DESC LIMIT 8
        """, (sid,))

    except Exception as e:
        log.error("analytics_overview failed: %s", e)
        return {"error": str(e)}

    return {
        "daily_messages": [{"day": str(r["day"]), "cnt": r["cnt"]} for r in daily],
        "top_categories": [{"category": r["category"], "cnt": r["cnt"]} for r in top_categories],
        "top_brands":     [{"brand": r["category"], "cnt": r["cnt"]} for r in top_categories],
        "top_models":     [{"name": r["name"], "stock": r["stock"]} for r in top_models],
        "unique_users":   uniq[0]["cnt"] if uniq else 0,
        "order_stats":    {r["status"]: r["cnt"] for r in order_stats},
    }


@router.get("/analytics/insights")
async def analytics_insights(shop: dict = Depends(get_current_shop)):
    """Конверсия, топ пустых запросов, средняя latency — из analytics_events."""
    from db import fetch_all, fetch_one_value, db_placeholder
    import config as _cfg
    import json as _json
    sid = shop["id"]
    ph = db_placeholder()
    try:
        interval = "NOW() - INTERVAL '30 days'" if _cfg.USE_POSTGRES else "datetime('now','-30 days')"
        payload_field = "payload" if _cfg.USE_POSTGRES else "payload"

        total = fetch_one_value(
            f"SELECT COUNT(*) FROM analytics_events WHERE shop_id = {ph} AND event_name = 'chat_reply' AND created_at >= {interval}",
            (sid,),
        ) or 0

        orders_count = fetch_one_value(
            f"SELECT COUNT(*) FROM orders WHERE shop_id = {ph} AND created_at >= {interval}",
            (sid,),
        ) or 0

        conversion = round(orders_count / total * 100, 1) if total > 0 else 0

        if _cfg.USE_POSTGRES:
            avg_latency = fetch_one_value(
                f"SELECT AVG((payload->>'latency_ms')::int) FROM analytics_events WHERE shop_id = {ph} AND event_name = 'chat_reply' AND created_at >= {interval}",
                (sid,),
            )
            missed_rows = fetch_all(
                f"""
                SELECT payload->>'user_message' AS query, COUNT(*) AS cnt
                FROM analytics_events
                WHERE shop_id = {ph}
                  AND event_name = 'chat_reply'
                  AND payload->>'mode' = 'catalog_not_found'
                  AND created_at >= {interval}
                GROUP BY query
                ORDER BY cnt DESC
                LIMIT 10
                """,
                (sid,),
            )
        else:
            avg_latency = None
            missed_rows = fetch_all(
                f"""
                SELECT json_extract(payload, '$.user_message') AS query, COUNT(*) AS cnt
                FROM analytics_events
                WHERE shop_id = {ph}
                  AND event_name = 'chat_reply'
                  AND json_extract(payload, '$.mode') = 'catalog_not_found'
                  AND created_at >= {interval}
                GROUP BY query
                ORDER BY cnt DESC
                LIMIT 10
                """,
                (sid,),
            )

        return {
            "period_days": 30,
            "total_messages": total,
            "orders_from_bot": orders_count,
            "conversion_pct": conversion,
            "avg_latency_ms": int(avg_latency) if avg_latency else None,
            "top_missed_queries": [
                {"query": r["query"], "cnt": r["cnt"]}
                for r in missed_rows if r.get("query")
            ],
        }
    except Exception as e:
        log.error("analytics_insights failed: %s", e)
        return {"error": str(e)}


@router.get("/stats")
async def shop_stats(shop: dict = Depends(get_current_shop)):
    from billing import subscription_usage

    sid = shop["id"]
    sub = get_shop_subscription_detail(sid)
    usage = subscription_usage(sid)
    return {
        "shop_id": sid,
        "products": count_shop_rows("products", sid),
        "orders": count_shop_rows("orders", sid),
        "conversations": count_shop_rows("conversations", sid),
        "messages": count_shop_messages(sid),
        "analytics_events": count_shop_rows("analytics_events", sid),
        "subscription": {**(sub or {}), **usage},
    }


@router.get("/products")
async def shop_products(limit: int = 100, offset: int = 0, shop: dict = Depends(get_current_shop)):
    sid = shop["id"]
    limit = max(1, min(limit, 500))
    return {
        "count": count_shop_rows("products", sid),
        "limit": limit,
        "offset": offset,
        "items": list_products(limit=limit, offset=offset, shop_id=sid),
    }


@router.patch("/products/{product_id}")
async def shop_update_product(product_id: int, body: ProductPatch, shop: dict = Depends(get_current_shop)):
    if body.price is not None and body.price <= 0:
        raise HTTPException(400, "price must be > 0")
    if body.quantity is not None and body.quantity < 0:
        raise HTTPException(400, "quantity cannot be negative")
    update_product(product_id, price=body.price, quantity=body.quantity, shop_id=shop["id"])
    return {"ok": True}


@router.get("/export")
async def shop_export(shop: dict = Depends(get_current_shop)):
    csv_text = products_to_csv(list_products(limit=10000, shop_id=shop["id"]))
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=catalog.csv"},
    )


@router.post("/import")
async def shop_import(file: UploadFile = File(...), replace: bool = False, shop: dict = Depends(require_verified_shop)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")
    content = await file.read()
    result = validate_product_csv(content)
    if not result["valid"]:
        raise HTTPException(400, "; ".join(result["errors"][:5]))
    imported = import_products(result["products"], replace=replace, shop_id=shop["id"])
    set_shop_data_source(shop["id"], "csv")
    log_analytics_event("dashboard", "products_imported", {"imported": imported, "replace": replace}, shop_id=shop["id"])
    return {"ok": True, "imported": imported}


@router.post("/import-preview")
async def shop_import_preview(file: UploadFile = File(...), shop: dict = Depends(require_verified_shop)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")
    content = await file.read()
    result = validate_product_csv(content)
    return {
        "valid": result["valid"],
        "valid_rows": len(result["products"]),
        "error_count": len(result["errors"]),
        "errors": result["errors"][:20],
        "preview": result["products"][:10],
    }


@router.get("/orders")
async def shop_orders(limit: int = 100, offset: int = 0, shop: dict = Depends(get_current_shop)):
    sid = shop["id"]
    return {
        "count": count_shop_rows("orders", sid),
        "items": list_orders(limit=limit, offset=offset, shop_id=sid),
    }


@router.patch("/orders/{order_id}")
async def shop_update_order(order_id: int, body: OrderPatch, shop: dict = Depends(get_current_shop)):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"status must be one of: {', '.join(ORDER_STATUSES)}")
    update_order_status(order_id, body.status, shop_id=shop["id"])
    return {"ok": True, "status": body.status}


@router.get("/messages")
async def shop_messages(limit: int = 50, shop: dict = Depends(get_current_shop)):
    limit = max(1, min(limit, 200))
    return {"items": list_recent_messages(limit=limit, shop_id=shop["id"])}


@router.patch("/settings")
async def shop_update_settings(body: SettingsUpdate, shop: dict = Depends(get_current_shop)):
    update_shop_settings(
        shop["id"],
        name=body.name,
        groq_system_prompt=body.groq_system_prompt,
        groq_api_key=body.groq_api_key,
        clear_groq_api_key=body.clear_groq_api_key,
        bot_role=body.bot_role,
        business_type=body.business_type,
        website_url=body.website_url,
        owner_telegram_chat_id=body.owner_telegram_chat_id,
    )
    updated = get_shop_by_id(shop["id"])
    if updated and updated.get("tg_token") and updated.get("tg_webhook_secret"):
        from telegram_bot import register_shop_bot

        await register_shop_bot(updated)
    return {"ok": True, "has_order_notify": bool((updated or {}).get("owner_telegram_chat_id"))}


@router.post("/change-password")
async def shop_change_password(body: PasswordChange, shop: dict = Depends(get_current_shop)):
    full = get_shop_by_email(shop["owner_email"])
    if not full or not verify_password(body.current_password, full.get("owner_password_hash") or ""):
        raise HTTPException(400, "Неверный текущий пароль")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Пароль должен быть не менее 8 символов")
    set_shop_owner_password(shop["id"], hash_password(body.new_password))
    # token_version just bumped — reissue so THIS dashboard session survives;
    # every other previously issued token is now rejected by get_current_shop.
    fresh = get_shop_by_id(shop["id"]) or {}
    token = create_shop_token(shop["id"], int(fresh.get("token_version") or 0))
    return {"ok": True, "token": token}


@router.get("/subscription")
async def shop_subscription(shop: dict = Depends(get_current_shop)):
    from billing import subscription_usage

    sub = get_shop_subscription_detail(shop["id"])
    if not sub:
        raise HTTPException(404, "Подписка не найдена")
    return {**sub, **subscription_usage(shop["id"])}


@router.post("/sandbox-chat")
async def shop_sandbox_chat(body: SandboxChatRequest, shop: dict = Depends(require_verified_shop)):
    """Owner-facing dry-run. Hits Groq with the shop's own catalog & API key
    but skips quota, persistence, analytics. Per-shop limit: 30 reqs/hour."""
    from ai import sandbox_reply

    message = (body.message or "").strip()
    if not message:
        raise HTTPException(400, "Введите сообщение для теста")
    if len(message) > 2000:
        raise HTTPException(400, "Сообщение слишком длинное (макс. 2000 символов)")
    _check_sandbox_rate(shop["id"])
    history = [
        {"role": m.role, "content": (m.content or "").strip()[:2000]}
        for m in (body.history or [])
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ][-16:]
    result = await sandbox_reply(shop["id"], message, history=history)
    return result


@router.get("/payment-info")
async def shop_payment_info(_: dict = Depends(get_current_shop)):
    """Payment requisites only — pricing is intentionally not exposed yet.
    Tiers will be re-added when plan upgrade self-service ships."""
    from config import PAYMENT_DETAILS, PAYMENT_KASPI
    return {
        "kaspi": PAYMENT_KASPI,
        "details": PAYMENT_DETAILS,
    }


# ── Telegram bot connection ────────────────────────────────────────────────────

class MoyskladTokenRequest(BaseModel):
    token: str


@router.post("/moysklad-connect")
async def shop_moysklad_connect(body: MoyskladTokenRequest, shop: dict = Depends(require_verified_shop)):
    """Save МойСклад API token and import full catalog."""
    from moysklad import sync_moysklad_catalog

    if not body.token.strip():
        raise HTTPException(400, "Токен не может быть пустым")
    token = body.token.strip()
    save_moysklad_token(shop["id"], token)
    set_shop_data_source(shop["id"], "moysklad")
    result = await sync_moysklad_catalog(token, shop["id"], replace=True)
    if result.get("error"):
        raise HTTPException(502, result["error"])
    if result["imported"] == 0:
        raise HTTPException(502, "МойСклад не вернул товары для импорта")
    return {"ok": True, "shop_id": shop["id"], "shop_name": shop["name"], **result}


@router.post("/moysklad-sync")
async def shop_moysklad_sync(replace: bool = False, shop: dict = Depends(require_verified_shop)):
    """Re-import catalog from МойСклад. replace=true removes products not in МойСклад."""
    from moysklad import sync_moysklad_catalog

    token = shop.get("moysklad_token")
    if not token:
        raise HTTPException(400, "МойСклад не подключён")
    result = await sync_moysklad_catalog(token, shop["id"], replace=replace)
    set_shop_data_source(shop["id"], "moysklad")
    if result.get("error"):
        raise HTTPException(502, result["error"])
    return {"ok": True, "shop_id": shop["id"], "shop_name": shop["name"], **result}


@router.delete("/moysklad-connect")
async def shop_moysklad_disconnect(shop: dict = Depends(require_verified_shop)):
    clear_moysklad_token(shop["id"])
    return {"ok": True}


@router.post("/sync-api-key")
async def shop_generate_api_key(shop: dict = Depends(require_verified_shop)):
    """Generate (or regenerate) a sync API key for this shop."""
    key = generate_sync_api_key(shop["id"])
    return {"ok": True, "api_key": key}


class BotConnectRequest(BaseModel):
    tg_token: str


@router.post("/bot-connect")
async def shop_bot_connect(body: BotConnectRequest, shop: dict = Depends(require_verified_shop)):
    """Validate Telegram token, register webhook, save to DB."""
    import secrets
    import httpx
    from config import TELEGRAM_WEBHOOK_URL

    token = body.tg_token.strip()
    if not token:
        raise HTTPException(400, "Введите токен бота")

    # Validate token with Telegram
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
    if r.status_code != 200:
        raise HTTPException(400, "Неверный токен — Telegram его не принял")
    bot_info = r.json().get("result", {})

    webhook_secret = secrets.token_urlsafe(32)
    ph = db_placeholder()
    detached = fetch_all(
        f"SELECT id, name FROM shops WHERE tg_token = {ph} AND id <> {ph}",
        (token, shop["id"]),
    )
    from telegram_bot import register_shop_bot, unregister_shop_bot

    for other in detached:
        await unregister_shop_bot(other["id"])

    save_shop_tg_token(shop["id"], token, webhook_secret)

    webhook_url: str | None = None
    if TELEGRAM_WEBHOOK_URL:
        webhook_url = TELEGRAM_WEBHOOK_URL.rstrip("/") + f"/tg/{webhook_secret}/webhook"
        async with httpx.AsyncClient(timeout=10) as client:
            # secret_token: Telegram echoes this back in X-Telegram-Bot-Api-Secret-Token
            # so the webhook handler can drop anything that didn't come from Telegram.
            await client.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                json={
                    "url": webhook_url,
                    "drop_pending_updates": True,
                    "secret_token": webhook_secret,
                },
            )

    updated_shop = get_shop_by_id(shop["id"])
    if updated_shop:
        await register_shop_bot(updated_shop)

    try:
        log_analytics_event(
            "telegram", "bot_connected",
            {"bot_username": bot_info.get("username")},
            shop_id=shop["id"],
        )
    except Exception:
        log.exception("bot_connected event failed shop=%s", shop["id"])

    return {
        "ok": True,
        "bot_username": bot_info.get("username"),
        "webhook_url": webhook_url,
        "detached_from": [{"id": s["id"], "name": s["name"]} for s in detached],
    }


@router.post("/bot-test-message")
async def shop_bot_test_message(shop: dict = Depends(require_verified_shop)):
    """Send a smoke-test message to the owner's Telegram using the connected bot.

    Lets the owner confirm at registration time that token + chat_id are wired up correctly,
    before customers start arriving. Refreshes shop state from DB to avoid stale JWT data.
    """
    import httpx

    fresh = get_shop_by_id(shop["id"]) or shop
    token = (fresh.get("tg_token") or "").strip()
    chat_id = (fresh.get("owner_telegram_chat_id") or "").strip()
    if not token:
        raise HTTPException(400, "Сначала подключите Telegram-бота")
    if not chat_id:
        raise HTTPException(400, "Сначала укажите Telegram ID для уведомлений и нажмите /start у бота")
    text = (
        f"✅ Тест прошёл — {fresh.get('name') or 'магазин'} подключён.\n"
        "Сюда будут приходить уведомления о заказах и алёрты по лимитам."
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(chat_id), "text": text},
            )
    except (ValueError, httpx.HTTPError) as e:
        raise HTTPException(502, f"Telegram отверг запрос: {e}") from None
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        detail = data.get("description") or f"HTTP {r.status_code}"
        if "chat not found" in detail.lower():
            detail = "Telegram говорит «чат не найден». Откройте бота и нажмите /start, потом повторите."
        raise HTTPException(400, detail)
    return {"ok": True}


@router.delete("/bot-connect")
async def shop_bot_disconnect(shop: dict = Depends(require_verified_shop)):
    """Remove Telegram bot from this shop."""
    from telegram_bot import shop_bots
    secret = shop.get("tg_webhook_secret")
    if secret and secret in shop_bots:
        bot, _, _ = shop_bots.pop(secret)
        try:
            await bot.delete_webhook()
            await bot.session.close()
        except Exception:
            pass
    clear_shop_tg_token(shop["id"])
    return {"ok": True}
