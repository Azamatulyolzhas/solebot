import logging
import sqlite3

from config import DATABASE_URL, DB_PATH, USE_POSTGRES

log = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

try:
    from psycopg_pool import ConnectionPool
except ImportError:
    ConnectionPool = None

_pool: "ConnectionPool | None" = None

# Pool size — Railway plans typically allow 100-200 max Postgres connections.
# Single web worker × 10 = comfortable headroom; bump max_size if you scale workers.
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 10


def _get_pool() -> "ConnectionPool":
    """Lazy-init a process-global connection pool. The previous implementation
    opened a fresh psycopg.connect() on every fetch_all / execute_write, which
    means a single Telegram message spends 12-16 round-trips on TCP handshakes
    + Postgres auth. With the pool, the same workload reuses warm connections."""
    global _pool
    if ConnectionPool is None:
        raise RuntimeError(
            "psycopg_pool is required for pooled Postgres connections. "
            "Install with: pip install psycopg-pool"
        )
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_db_pool() -> None:
    """Shut the pool down cleanly (call from FastAPI lifespan on shutdown)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            log.exception("Closing connection pool failed")
        _pool = None


class _PooledConnection:
    """Mimic the plain psycopg.Connection API but `.close()` returns the
    underlying connection to the pool instead of dropping it. This lets every
    existing caller (fetch_all, execute_write, etc.) keep its
    `conn = get_db(); try: ...; finally: conn.close()` pattern unchanged.

    Also supports `with get_db() as conn:` and falls back to .close() on GC,
    so a forgotten finally block can't permanently leak a pool slot.
    """

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
        self._returned = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        # Last-resort safety net for a forgotten conn.close(). Idempotent
        # via the _returned flag, so a normal close() before GC is unaffected.
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        if self._returned:
            return
        self._returned = True
        try:
            self._pool.putconn(self._conn)
        except Exception:
            log.exception("Returning connection to pool failed; closing it instead")
            try:
                self._conn.close()
            except Exception:
                pass


def get_db():
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL")
        pool = _get_pool()
        return _PooledConnection(pool, pool.getconn())

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_placeholder() -> str:
    return "%s" if USE_POSTGRES else "?"


def fetch_all(query: str, params: list | tuple = ()) -> list[dict]:
    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_one_value(query: str, params: list | tuple = ()):
    conn = get_db()
    row = conn.execute(query, params).fetchone()
    conn.close()
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def execute_write(query: str, params: list | tuple = (), fetch_one: bool = False):
    conn = get_db()
    try:
        cur = conn.execute(query, params)
        row = cur.fetchone() if fetch_one else None
        conn.commit()
        if row is None:
            return None
        return dict(row) if not isinstance(row, tuple) else row
    finally:
        conn.close()
