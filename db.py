import sqlite3

from config import DATABASE_URL, DB_PATH, USE_POSTGRES

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


def get_db():
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

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
