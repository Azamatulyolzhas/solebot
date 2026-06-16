import os

from dotenv import load_dotenv


load_dotenv()

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
if not JWT_SECRET:
    import secrets as _secrets
    JWT_SECRET = _secrets.token_hex(32)
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "JWT_SECRET not set in .env — using random ephemeral key. "
        "All sessions will be invalidated on restart. Set JWT_SECRET in production!"
    )
JWT_ALGORITHM = "HS256"
JWT_TTL_DAYS = int(os.getenv("JWT_TTL_DAYS", "30"))

# Subscription plans (shown in shop dashboard)
PAYMENT_KASPI = os.getenv("PAYMENT_KASPI", "")        # Kaspi Gold number
PAYMENT_DETAILS = os.getenv("PAYMENT_DETAILS", "")    # Extra payment instructions

# Email notifications via Resend (https://resend.com)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@solebot.app")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "SaleBot")
SHOP_DASHBOARD_URL = os.getenv("SHOP_DASHBOARD_URL", "")
