import os

from dotenv import load_dotenv


load_dotenv()

_explicit_env = (os.getenv("ENVIRONMENT") or "").strip().lower()
_railway_env = (os.getenv("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
ENVIRONMENT = _explicit_env or _railway_env or "development"
# Fail-safe: production guards activate if EITHER signal says prod. Stops an
# operator from accidentally disabling fail-fast by setting ENVIRONMENT=staging
# on a Railway production deploy.
IS_PRODUCTION = (
    _explicit_env in ("production", "prod")
    or _railway_env in ("production", "prod")
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Model for the consultant bot. The small 8B (llama-3.1-8b-instant) is being
# retired and may be unavailable, so we standardize on a single 70B-class model
# for everything. Override via env if a different model is provisioned.
#   GROQ_MODEL=llama-3.3-70b-versatile
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
# Intent classification used to run on a separate cheap 8B model. That model is
# no longer guaranteed to exist, so the classifier now defaults to GROQ_MODEL.
# (Latency is kept in check by routing most messages deterministically before
# ever calling the classifier — see ai.py.)
GROQ_CLASSIFIER_MODEL = os.getenv("GROQ_CLASSIFIER_MODEL", GROQ_MODEL).strip()

# LLM "brain": one structured call per turn that sees the whole catalog + history
# and decides what to show / answer / order, instead of the keyword routing chain.
# Off by default (the deterministic chain stays the fallback and keeps tests valid);
# turn on in prod with AI_BRAIN=1. Facts/orders stay deterministic, so a higher
# temperature only makes the wording livelier, not the facts.
AI_BRAIN = os.getenv("AI_BRAIN", "0").strip().lower() not in ("", "0", "false", "no")
try:
    GROQ_BRAIN_TEMPERATURE = float(os.getenv("GROQ_BRAIN_TEMPERATURE", "0.5"))
except ValueError:
    GROQ_BRAIN_TEMPERATURE = 0.5

# Cap on how many catalog items get serialized into a single LLM prompt (the brain
# and the semantic search). Keeps prompt size — and therefore Groq token cost and
# 429 pressure — BOUNDED as the catalog grows: above this count we pre-filter to
# query-relevant candidates with the free keyword search instead of sending the
# whole catalog. Set high enough to be a no-op for small catalogs.
LLM_CATALOG_MAX_ITEMS = int(os.getenv("LLM_CATALOG_MAX_ITEMS", "120"))
# Max seconds we'll honor a Groq 429 `Retry-After` for a single in-line retry. A
# longer wait means the per-minute bucket is blown — we give up to the
# deterministic catalog fallback rather than hold the customer's reply hostage.
try:
    GROQ_RETRY_MAX_WAIT = float(os.getenv("GROQ_RETRY_MAX_WAIT", "2.0"))
except ValueError:
    GROQ_RETRY_MAX_WAIT = 2.0

# ── Резервный LLM-провайдер (отказоустойчивость) ──────────────────────────────────
# Второй OpenAI-совместимый chat-endpoint, который пробуется, когда Groq упал
# (rate-limit / HTTP / обрыв связи). По умолчанию ВЫКЛЮЧЕН — задай все три значения,
# чтобы включить. Примеры:
#   OpenRouter: FALLBACK_BASE_URL=https://openrouter.ai/api/v1
#               FALLBACK_MODEL=meta-llama/llama-3.3-70b-instruct
#   Mistral:    FALLBACK_BASE_URL=https://api.mistral.ai/v1
#               FALLBACK_MODEL=mistral-large-latest
# ВАЖНО: для «мозга» модель должна поддерживать JSON-режим (response_format).
FALLBACK_BASE_URL = os.getenv("FALLBACK_BASE_URL", "").strip()
FALLBACK_API_KEY = os.getenv("FALLBACK_API_KEY", "").strip()
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "").strip()

# ── Алерт на всплеск деградированных ответов (Groq штормит) ────────────────────────
# Когда доля degraded_* ответов магазина за окно WINDOW_SEC превышает RATIO при
# минимум MIN_SAMPLES ответов — владельцу уходит одно уведомление, не чаще раза в
# COOLDOWN_SEC. Дёшево: проверка читает БД максимум раз за кулдаун и только с
# degraded-хода (см. notifications.maybe_alert_degradation_spike).
try:
    DEGRADED_ALERT_RATIO = float(os.getenv("DEGRADED_ALERT_RATIO", "0.2"))
except ValueError:
    DEGRADED_ALERT_RATIO = 0.2
DEGRADED_ALERT_MIN_SAMPLES = int(os.getenv("DEGRADED_ALERT_MIN_SAMPLES", "10"))
DEGRADED_ALERT_WINDOW_SEC = int(os.getenv("DEGRADED_ALERT_WINDOW_SEC", "600"))
DEGRADED_ALERT_COOLDOWN_SEC = int(os.getenv("DEGRADED_ALERT_COOLDOWN_SEC", "1800"))

# ── Embeddings / RAG semantic search ────────────────────────────────────────────
# A hosted, OpenAI-compatible /v1/embeddings endpoint (default OpenAI). Vector
# search activates only when USE_POSTGRES is on AND EMBEDDING_API_KEY is set —
# otherwise the deterministic keyword/translit search stays the path, so dev and
# tests are unaffected. Keep EMBEDDING_DIM at 384 to match the existing
# pgvector(384) column (schema.py) — no DB migration needed. text-embedding-3-*
# honour the `dimensions` request param, so 384 output is a config flag, not a
# different model. Point BASE_URL/MODEL at Jina/Gemini/etc. if preferred.
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "").strip()
EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL", "https://api.openai.com/v1"
).strip().rstrip("/")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
try:
    EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
except ValueError:
    EMBEDDING_DIM = 384
# How many model-families RAG retrieves into the LLM-brain prompt per turn, in
# place of dumping the whole (bounded) catalog. This is the main token saver that
# eases Groq 429 pressure. Browse queries ('что есть') and catalogs at/under this
# size keep the old full-context behaviour.
try:
    BRAIN_RETRIEVAL_TOPK = int(os.getenv("BRAIN_RETRIEVAL_TOPK", "20"))
except ValueError:
    BRAIN_RETRIEVAL_TOPK = 20

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")
# Optional secret echoed back by Telegram in X-Telegram-Bot-Api-Secret-Token
# for the default-bot webhook. Per-shop bots manage their own secret.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
DB_PATH = os.getenv("DB_PATH", "sneakers.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)

DEFAULT_SHOP_SLUG = os.getenv("SHOP_SLUG", "default")
DEFAULT_SHOP_NAME = os.getenv("SHOP_NAME", "Default shop")

REDIS_URL = os.getenv("REDIS_URL", "")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
RATE_LIMIT_MESSAGES = int(os.getenv("RATE_LIMIT_MESSAGES", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# JWT secret for shop owner tokens — set a strong random string in .env
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET and not IS_PRODUCTION:
    import secrets as _secrets
    JWT_SECRET = _secrets.token_hex(32)
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "JWT_SECRET not set — using random ephemeral key (dev mode). "
        "All sessions will be invalidated on restart."
    )
JWT_ALGORITHM = "HS256"
JWT_TTL_DAYS = int(os.getenv("JWT_TTL_DAYS", "30"))

# Email verification kill-switch. Default OFF until Resend domain is verified
# and we're ready to gate bot-connect / catalog import on a real email click.
# Set EMAIL_VERIFICATION_ENABLED=true in env to re-enable the gate; new signups
# will then start with email_verified=FALSE again.
EMAIL_VERIFICATION_ENABLED = os.getenv(
    "EMAIL_VERIFICATION_ENABLED", "false"
).strip().lower() in ("true", "1", "yes", "on")

# Subscription plans (shown in shop dashboard)
PAYMENT_KASPI = os.getenv("PAYMENT_KASPI", "")        # Kaspi Gold number
PAYMENT_DETAILS = os.getenv("PAYMENT_DETAILS", "")    # Extra payment instructions

# Sentry (https://sentry.io). Leave empty in dev — init is gated on this being set.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))

# Support contacts shown in dashboard + landing footer. Both optional — footer
# hides entries that are not set.
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
SUPPORT_TELEGRAM = os.getenv("SUPPORT_TELEGRAM", "")  # e.g. "vendly_support" (no @)

# Email notifications via Resend (https://resend.com)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@solebot.app")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Vendly")
SHOP_DASHBOARD_URL = os.getenv("SHOP_DASHBOARD_URL", "")

# CORS — comma-separated list of allowed origins. Empty in dev means "allow all without credentials".
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]


def _require_production_secrets() -> None:
    """In production, every secret on this list must be set. Fail fast with one message
    listing all the missing names instead of letting the app boot half-configured and
    crash later under traffic."""
    missing: list[str] = []
    if not JWT_SECRET:
        missing.append("JWT_SECRET")
    if not ADMIN_PASSWORD_HASH:
        missing.append("ADMIN_PASSWORD_HASH")
    if not ALLOWED_ORIGINS:
        missing.append("ALLOWED_ORIGINS")
    if missing:
        raise RuntimeError(
            f"Missing required env vars for production: {', '.join(missing)}. "
            f"Set them in your environment (ENVIRONMENT=production was detected). "
            f"For local dev, leave ENVIRONMENT unset or set it to 'development'."
        )


if IS_PRODUCTION:
    _require_production_secrets()
