"""Onboarding drip-email processor.

Designed to be called hourly (or daily) by an external cron via the
admin endpoint POST /admin/cron/onboarding. Idempotent: every send is
gated by a (shop_id, kind) UNIQUE constraint in email_sent_log, so the
same email won't go twice even if the cron fires twice.

Two emails after the day-0 welcome:
  - onboarding_day3:  +3 days after signup. Content depends on whether
                      Telegram + catalog are connected.
  - onboarding_day10: trial-ending warning when 1-4 days remain.

We never send if the shop:
  - has an unverified email (they haven't confirmed they own the address)
  - is not status='active' (suspended/deleted)
  - hasn't supplied owner_email at all (default-shop, legacy)
"""
import logging

from config import USE_POSTGRES
from db import db_placeholder, execute_write, fetch_all

log = logging.getLogger(__name__)


def _try_claim_email(shop_id: int, kind: str) -> bool:
    """Insert a (shop_id, kind) row. Returns True iff inserted (i.e. not seen before).

    Race-safe because the UNIQUE constraint at the DB layer makes duplicate
    inserts fail; we treat the failure as "already sent" and return False.
    """
    ph = db_placeholder()
    try:
        execute_write(
            f"INSERT INTO email_sent_log (shop_id, kind) VALUES ({ph}, {ph})",
            (shop_id, kind),
        )
        return True
    except Exception as e:
        # Both Postgres UniqueViolation and SQLite IntegrityError land here.
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "constraint" in msg:
            return False
        log.exception("email_sent_log insert failed unexpectedly shop=%s kind=%s", shop_id, kind)
        return False


def _find_day3_candidates() -> list[dict]:
    """Verified, active shops that registered 3+ days ago and haven't received day3."""
    if USE_POSTGRES:
        rows = fetch_all("""
            SELECT s.id, s.name, s.owner_email,
                   (s.tg_token IS NOT NULL AND s.tg_token <> '') AS has_tg_bot,
                   EXISTS(SELECT 1 FROM products p WHERE p.shop_id = s.id) AS has_catalog
            FROM shops s
            WHERE s.status = 'active'
              AND s.email_verified = TRUE
              AND s.owner_email IS NOT NULL AND s.owner_email <> ''
              AND s.created_at <= NOW() - INTERVAL '3 days'
              AND NOT EXISTS (
                SELECT 1 FROM email_sent_log e
                WHERE e.shop_id = s.id AND e.kind = 'onboarding_day3'
              )
        """)
    else:
        rows = fetch_all("""
            SELECT s.id, s.name, s.owner_email,
                   CASE WHEN s.tg_token IS NOT NULL AND s.tg_token <> '' THEN 1 ELSE 0 END AS has_tg_bot,
                   (SELECT COUNT(*) FROM products p WHERE p.shop_id = s.id LIMIT 1) AS has_catalog
            FROM shops s
            WHERE s.status = 'active'
              AND s.email_verified = 1
              AND s.owner_email IS NOT NULL AND s.owner_email <> ''
              AND datetime(s.created_at) <= datetime('now', '-3 days')
              AND NOT EXISTS (
                SELECT 1 FROM email_sent_log e
                WHERE e.shop_id = s.id AND e.kind = 'onboarding_day3'
              )
        """)
    return rows


def _find_day10_candidates() -> list[dict]:
    """Trial shops with 1-4 days left that haven't received day10."""
    if USE_POSTGRES:
        rows = fetch_all("""
            SELECT s.id, s.name, s.owner_email,
                   CAST(EXTRACT(DAY FROM (sub.trial_ends_at - NOW())) AS INTEGER) AS days_left
            FROM shops s
            JOIN subscriptions sub ON sub.shop_id = s.id
            WHERE s.status = 'active'
              AND s.email_verified = TRUE
              AND s.owner_email IS NOT NULL AND s.owner_email <> ''
              AND sub.plan = 'trial'
              AND sub.status = 'active'
              AND sub.trial_ends_at IS NOT NULL
              AND sub.trial_ends_at > NOW()
              AND sub.trial_ends_at <= NOW() + INTERVAL '4 days'
              AND NOT EXISTS (
                SELECT 1 FROM email_sent_log e
                WHERE e.shop_id = s.id AND e.kind = 'onboarding_day10'
              )
        """)
    else:
        rows = fetch_all("""
            SELECT s.id, s.name, s.owner_email,
                   CAST((julianday(sub.trial_ends_at) - julianday('now')) AS INTEGER) AS days_left
            FROM shops s
            JOIN subscriptions sub ON sub.shop_id = s.id
            WHERE s.status = 'active'
              AND s.email_verified = 1
              AND s.owner_email IS NOT NULL AND s.owner_email <> ''
              AND sub.plan = 'trial'
              AND sub.status = 'active'
              AND sub.trial_ends_at IS NOT NULL
              AND datetime(sub.trial_ends_at) > datetime('now')
              AND datetime(sub.trial_ends_at) <= datetime('now', '+4 days')
              AND NOT EXISTS (
                SELECT 1 FROM email_sent_log e
                WHERE e.shop_id = s.id AND e.kind = 'onboarding_day10'
              )
        """)
    return rows


def process_pending_onboarding_emails() -> dict:
    """Find + send pending onboarding emails. Returns counts per kind."""
    from email_service import send_onboarding_day3, send_onboarding_day10

    day3_sent = 0
    for shop in _find_day3_candidates():
        if not _try_claim_email(shop["id"], "onboarding_day3"):
            continue
        try:
            send_onboarding_day3(
                shop["name"] or "Магазин",
                shop["owner_email"],
                has_tg_bot=bool(shop.get("has_tg_bot")),
                has_catalog=bool(shop.get("has_catalog")),
            )
            day3_sent += 1
        except Exception:
            log.exception("send_onboarding_day3 failed shop=%s", shop["id"])

    day10_sent = 0
    for shop in _find_day10_candidates():
        if not _try_claim_email(shop["id"], "onboarding_day10"):
            continue
        try:
            days_left = max(1, int(shop.get("days_left") or 1))
            send_onboarding_day10(
                shop["name"] or "Магазин",
                shop["owner_email"],
                days_left=days_left,
            )
            day10_sent += 1
        except Exception:
            log.exception("send_onboarding_day10 failed shop=%s", shop["id"])

    return {"day3_sent": day3_sent, "day10_sent": day10_sent}
