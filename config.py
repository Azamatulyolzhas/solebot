import os

from dotenv import load_dotenv


load_dotenv()

ENVIRONMENT = (
    os.getenv("ENVIRONMENT")
    or os.getenv("RAILWAY_ENVIRONMENT_NAME")
    or "development"
).strip().lower()
IS_PRODUCTION = ENVIRONMENT in ("production", "prod")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "")

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

# Subscription plans (shown in shop dashboard)
PAYMENT_KASPI = os.getenv("PAYMENT_KASPI", "")        # Kaspi Gold number
PAYMENT_DETAILS = os.getenv("PAYMENT_DETAILS", "")    # Extra payment instructions

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
