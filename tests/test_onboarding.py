"""Onboarding drip-email tests.

Idempotency comes from the UNIQUE(shop_id, kind) constraint on
email_sent_log; we exercise both the happy path (one send per shop per
kind) and the re-run case (a second cron call doesn't double-send).
"""
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

import db as db_module
import onboarding


@pytest.fixture
def isolated_db(monkeypatch):
    """Spin up a fresh SQLite file + minimal schema for the funnel queries."""
    import os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db_module, "DB_PATH", path)
    monkeypatch.setattr(db_module, "USE_POSTGRES", False)
    monkeypatch.setattr(onboarding, "USE_POSTGRES", False)

    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            owner_email TEXT,
            tg_token TEXT,
            email_verified INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL
        );
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            plan TEXT NOT NULL DEFAULT 'trial',
            status TEXT NOT NULL DEFAULT 'active',
            trial_ends_at TIMESTAMP
        );
        CREATE TABLE email_sent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (shop_id, kind)
        );
    """)
    conn.commit()
    conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


class TestDay3Eligibility:

    def test_picks_only_verified_active_old_shops(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.executescript("""
            INSERT INTO shops (id, name, owner_email, email_verified, created_at)
              VALUES (1, 'Old Verified', 'a@b.com', 1, datetime('now', '-4 days'));
            INSERT INTO shops (id, name, owner_email, email_verified, created_at)
              VALUES (2, 'Old Unverified', 'u@b.com', 0, datetime('now', '-4 days'));
            INSERT INTO shops (id, name, owner_email, email_verified, created_at)
              VALUES (3, 'Fresh', 'f@b.com', 1, datetime('now', '-1 day'));
            INSERT INTO shops (id, name, owner_email, email_verified, status, created_at)
              VALUES (4, 'Suspended', 's@b.com', 1, 'suspended', datetime('now', '-5 days'));
        """)
        conn.commit()
        conn.close()

        candidates = onboarding._find_day3_candidates()
        ids = {c["id"] for c in candidates}
        assert ids == {1}

    def test_excludes_shops_already_emailed(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.executescript("""
            INSERT INTO shops (id, name, owner_email, email_verified, created_at)
              VALUES (1, 'A', 'a@b.com', 1, datetime('now', '-4 days'));
            INSERT INTO email_sent_log (shop_id, kind) VALUES (1, 'onboarding_day3');
        """)
        conn.commit()
        conn.close()

        assert onboarding._find_day3_candidates() == []


class TestDay10Eligibility:

    def test_picks_trials_ending_within_4_days(self, isolated_db):
        conn = sqlite3.connect(isolated_db)
        conn.executescript("""
            INSERT INTO shops (id, name, owner_email, email_verified)
              VALUES (1, 'Ending soon', 'a@b.com', 1);
            INSERT INTO subscriptions (shop_id, plan, status, trial_ends_at)
              VALUES (1, 'trial', 'active', datetime('now', '+2 days'));

            INSERT INTO shops (id, name, owner_email, email_verified)
              VALUES (2, 'Plenty of time', 'b@b.com', 1);
            INSERT INTO subscriptions (shop_id, plan, status, trial_ends_at)
              VALUES (2, 'trial', 'active', datetime('now', '+12 days'));

            INSERT INTO shops (id, name, owner_email, email_verified)
              VALUES (3, 'Already expired', 'c@b.com', 1);
            INSERT INTO subscriptions (shop_id, plan, status, trial_ends_at)
              VALUES (3, 'trial', 'active', datetime('now', '-1 day'));

            INSERT INTO shops (id, name, owner_email, email_verified)
              VALUES (4, 'Paid customer', 'd@b.com', 1);
            INSERT INTO subscriptions (shop_id, plan, status, trial_ends_at)
              VALUES (4, 'basic', 'active', datetime('now', '+2 days'));
        """)
        conn.commit()
        conn.close()

        candidates = onboarding._find_day10_candidates()
        ids = {c["id"] for c in candidates}
        assert ids == {1}


class TestClaimIdempotency:

    def test_claim_succeeds_once_then_fails(self, isolated_db):
        first = onboarding._try_claim_email(1, "onboarding_day3")
        second = onboarding._try_claim_email(1, "onboarding_day3")
        assert first is True
        assert second is False, "second claim must be rejected by UNIQUE constraint"


class TestProcessPendingFullFlow:

    def test_send_then_rerun_is_noop(self, isolated_db, monkeypatch):
        conn = sqlite3.connect(isolated_db)
        conn.executescript("""
            INSERT INTO shops (id, name, owner_email, email_verified, tg_token, created_at)
              VALUES (1, 'NoCatalog', 'a@b.com', 1, '', datetime('now', '-4 days'));
        """)
        conn.commit()
        conn.close()

        calls = {"day3": 0, "day10": 0}
        monkeypatch.setattr(
            "email_service.send_onboarding_day3",
            lambda *a, **kw: calls.__setitem__("day3", calls["day3"] + 1) or True,
        )
        monkeypatch.setattr(
            "email_service.send_onboarding_day10",
            lambda *a, **kw: calls.__setitem__("day10", calls["day10"] + 1) or True,
        )

        first = onboarding.process_pending_onboarding_emails()
        assert first["day3_sent"] == 1
        assert calls["day3"] == 1

        # Re-run — UNIQUE constraint already filled by first run.
        second = onboarding.process_pending_onboarding_emails()
        assert second["day3_sent"] == 0
        assert calls["day3"] == 1, "second run must not re-send"
