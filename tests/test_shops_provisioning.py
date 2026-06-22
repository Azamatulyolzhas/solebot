"""Unit tests for shops.create_active_shop_with_trial.

DB access (get_db) is monkeypatched so the slug-generation and atomic
commit/rollback logic is tested without touching any real database.

NOTE: create_active_shop_with_trial does ``from db import get_db`` inside
the function body, so we must patch ``db.get_db`` (the source), not
``shops.get_db`` (which does not exist at the module level in shops.py).
"""
import db
import shops


# ── Fake connection helpers ────────────────────────────────────────────────────

class _FakeCursor:
    """Minimal cursor that records executed SQL and returns a configurable lastrowid."""

    def __init__(self, lastrowid=42):
        self.lastrowid = lastrowid
        self.executed = []

    def fetchone(self):
        # Postgres branch expects row["id"]; tests run SQLite path (USE_POSTGRES=False)
        return None


class _FakeConn:
    """Minimal connection whose execute() records calls and returns a _FakeCursor."""

    def __init__(self, lastrowid=42, raise_on_execute_n=None):
        self._lastrowid = lastrowid
        self._raise_on_execute_n = raise_on_execute_n  # 1-based index of execute to fail
        self._execute_count = 0
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.executed_sqls = []

    def execute(self, sql, params=()):
        self._execute_count += 1
        self.executed_sqls.append(sql.strip())
        if self._raise_on_execute_n and self._execute_count == self._raise_on_execute_n:
            raise RuntimeError("Simulated DB error on execute #%d" % self._execute_count)
        cur = _FakeCursor(lastrowid=self._lastrowid)
        return cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ── TestCreateActiveShopWithTrial ──────────────────────────────────────────────

class TestCreateActiveShopWithTrial:

    def test_create_active_shop_with_trial_returns_id(self, monkeypatch):
        fake_conn = _FakeConn(lastrowid=42)
        # Patch db.get_db because the function imports it locally: ``from db import get_db``.
        monkeypatch.setattr(db, "get_db", lambda: fake_conn)
        # USE_POSTGRES must be False so the SQLite branch (lastrowid) is taken.
        monkeypatch.setattr(shops, "USE_POSTGRES", False)

        result = shops.create_active_shop_with_trial(
            name="My Shop",
            email="owner@example.com",
            password_hash="hashedpwd",
        )

        assert result == 42

    def test_slug_uses_token_hex_not_timestamp(self, monkeypatch):
        """Two calls in the same second must produce different slugs."""
        slugs = []

        def capture_slug_conn():
            # Capture the slug from the SQL params by letting real execute run.
            conn = _FakeConn(lastrowid=1)
            original_execute = conn.execute

            def spy_execute(sql, params=()):
                # First execute is the INSERT INTO shops; slug is params[1].
                if "INSERT INTO shops" in sql and params:
                    slugs.append(params[1])
                return original_execute(sql, params)

            conn.execute = spy_execute
            return conn

        monkeypatch.setattr(db, "get_db", capture_slug_conn)
        monkeypatch.setattr(shops, "USE_POSTGRES", False)

        shops.create_active_shop_with_trial("TestShop", "a@b.com", "h1")
        shops.create_active_shop_with_trial("TestShop", "a@b.com", "h2")

        assert len(slugs) == 2
        assert slugs[0] != slugs[1]

    def test_rollback_on_subscription_failure(self, monkeypatch):
        """When the 2nd execute (subscription INSERT) raises, rollback is called and None returned."""
        fake_conn = _FakeConn(lastrowid=10, raise_on_execute_n=2)
        monkeypatch.setattr(db, "get_db", lambda: fake_conn)
        monkeypatch.setattr(shops, "USE_POSTGRES", False)

        result = shops.create_active_shop_with_trial(
            name="Bad Shop",
            email="bad@example.com",
            password_hash="hash",
        )

        assert result is None
        assert fake_conn.rolled_back is True

    def test_trial_days_default_is_14(self):
        assert shops.TRIAL_DAYS_DEFAULT == 14
