"""JWT authentication and password hashing for shop owner portal."""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from config import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    ADMIN_PASSWORD_HASH,
    JWT_ALGORITHM,
    JWT_SECRET,
    JWT_TTL_DAYS,
)

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

def create_shop_token(shop_id: int, token_version: int = 0) -> str:
    payload = {
        "sub": str(shop_id),
        "tv": int(token_version or 0),
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_TTL_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_shop_claims(token: str) -> tuple[int, int] | None:
    """Return (shop_id, token_version) from a token, or None if invalid/expired.

    Tokens issued before versioning carry no "tv" claim and count as version 0,
    so sessions that predate the rollout stay valid until the owner changes
    their password (which bumps shops.token_version past 0).
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("role") == "admin":
            return None
        return int(payload["sub"]), int(payload.get("tv") or 0)
    except Exception:
        return None


def decode_shop_token(token: str) -> int | None:
    """Return shop_id from token, or None if invalid/expired."""
    claims = decode_shop_claims(token)
    return claims[0] if claims else None


def verify_admin_credentials(email: str, password: str) -> bool:
    if not ADMIN_EMAIL or not password:
        return False
    if email.strip().lower() != ADMIN_EMAIL:
        return False
    if ADMIN_PASSWORD_HASH and verify_password(password, ADMIN_PASSWORD_HASH):
        return True
    # plaintext ADMIN_PASSWORD fallback intentionally removed — use ADMIN_PASSWORD_HASH
    from shops import get_shop_by_email

    shop = get_shop_by_email(ADMIN_EMAIL)
    pwd_hash = (shop or {}).get("owner_password_hash") or ""
    return bool(pwd_hash and verify_password(password, pwd_hash))


def create_admin_token() -> str:
    payload = {
        "sub": "admin",
        "role": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_TTL_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_admin_token(token: str) -> bool:
    """Return True if token is a valid platform admin JWT."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("role") == "admin" and payload.get("sub") == "admin"
    except Exception:
        return False
