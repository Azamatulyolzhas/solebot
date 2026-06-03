"""JWT authentication and password hashing for shop owner portal."""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from config import JWT_ALGORITHM, JWT_SECRET, JWT_TTL_DAYS

log = logging.getLogger(__name__)

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Passwords ──────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


# ── JWT tokens ─────────────────────────────────────────────────────────────────

def create_shop_token(shop_id: int) -> str:
    payload = {
        "sub": str(shop_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_TTL_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_shop_token(token: str) -> int | None:
    """Return shop_id from token, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except Exception:
        return None
