"""Verify the _PooledConnection wrapper returns connections to the pool.

We don't spin up a real Postgres — the wrapper's contract is what matters:
- attribute access falls through to the underlying psycopg.Connection
- close() calls pool.putconn() exactly once, even if called twice
- close() falls back to conn.close() if putconn raises

The SQLite path is untouched by P3 item 1 and remains the default for tests.
"""
import db


class _FakePool:
    def __init__(self):
        self.returned: list = []
        self.put_raises: Exception | None = None

    def putconn(self, conn):
        if self.put_raises:
            raise self.put_raises
        self.returned.append(conn)


class _FakeConn:
    def __init__(self):
        self.closed = False

    def execute(self, query, params=()):
        return f"executed:{query}"

    def commit(self):
        return "committed"

    def close(self):
        self.closed = True


class TestPooledConnection:

    def test_attribute_delegation(self):
        pool, conn = _FakePool(), _FakeConn()
        wrapper = db._PooledConnection(pool, conn)
        assert wrapper.execute("SELECT 1") == "executed:SELECT 1"
        assert wrapper.commit() == "committed"

    def test_close_returns_to_pool(self):
        pool, conn = _FakePool(), _FakeConn()
        wrapper = db._PooledConnection(pool, conn)
        wrapper.close()
        assert pool.returned == [conn], "expected conn to be returned to pool"
        assert conn.closed is False, "conn must not be hard-closed when put-back succeeded"

    def test_close_is_idempotent(self):
        pool, conn = _FakePool(), _FakeConn()
        wrapper = db._PooledConnection(pool, conn)
        wrapper.close()
        wrapper.close()
        assert pool.returned == [conn], "second close must not double-return"

    def test_close_falls_back_to_hard_close_on_putconn_error(self):
        pool, conn = _FakePool(), _FakeConn()
        pool.put_raises = RuntimeError("pool dead")
        wrapper = db._PooledConnection(pool, conn)
        wrapper.close()
        assert conn.closed is True, "wrapper must hard-close conn if pool refuses it"


class TestSqlitePathUnchanged:

    def test_sqlite_get_db_returns_native_connection(self, monkeypatch):
        # The SQLite branch in get_db() must not go through the pool wrapper —
        # it stays exactly as it was so the test suite keeps running offline.
        monkeypatch.setattr(db, "USE_POSTGRES", False)
        conn = db.get_db()
        try:
            assert not isinstance(conn, db._PooledConnection)
            cur = conn.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
        finally:
            conn.close()


class TestReleaseReadConnection:
    """Reads must end their implicit transaction before returning to the pool,
    so psycopg_pool stops logging 'rolling back returned connection [INTRANS]'."""

    def test_rolls_back_then_closes(self):
        calls: list = []

        class C:
            def rollback(self):
                calls.append("rollback")

            def close(self):
                calls.append("close")

        db._release_read(C())
        assert calls == ["rollback", "close"]

    def test_closes_even_if_rollback_raises(self):
        calls: list = []

        class C:
            def rollback(self):
                raise RuntimeError("no txn")

            def close(self):
                calls.append("close")

        db._release_read(C())
        assert calls == ["close"]


class TestCloseDbPoolIdempotent:

    def test_close_when_uninitialised_is_noop(self):
        # If get_db() never ran (e.g. test process exits early), close_db_pool
        # must not blow up.
        db._pool = None
        db.close_db_pool()  # must not raise
        assert db._pool is None
