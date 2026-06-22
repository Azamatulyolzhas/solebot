"""Logical DB backup — dump whitelisted tables as CSV-in-ZIP.

This is a portable export, not a binary pg_dump. It's enough to recover
shop data into a fresh schema or hand a shop their own data on request,
without needing psql/pg_dump in the runtime image.

For production-grade infrastructure backups, also enable Railway's
built-in Postgres backups in the Railway dashboard. This file is the
belt-and-suspenders layer.
"""
import csv
import io
import logging
import zipfile
from datetime import datetime

from db import db_placeholder, fetch_all

log = logging.getLogger(__name__)

# Whitelist — only these tables get dumped. Add new tables here intentionally
# so a renamed/removed schema doesn't silently break the backup.
_PLATFORM_TABLES = (
    "shops",
    "subscriptions",
    "products",
    "conversations",
    "messages",
    "orders",
    "analytics_events",
    "password_reset_tokens",
    "email_verification_tokens",
)

# Tables that have a shop_id column — for per-shop backups we filter by it.
_SHOP_SCOPED_TABLES = (
    "subscriptions",
    "products",
    "conversations",
    "orders",
    "analytics_events",
    "password_reset_tokens",
    "email_verification_tokens",
)


def _dump_table_to_csv(table: str, shop_id: int | None) -> str:
    """SELECT * from a table (optionally scoped to shop_id) → CSV string."""
    ph = db_placeholder()
    if shop_id is not None and table in _SHOP_SCOPED_TABLES:
        rows = fetch_all(f"SELECT * FROM {table} WHERE shop_id = {ph}", (shop_id,))
    elif shop_id is not None and table == "shops":
        rows = fetch_all(f"SELECT * FROM shops WHERE id = {ph}", (shop_id,))
    elif shop_id is not None and table == "messages":
        # messages → conversations.shop_id, no direct shop_id column.
        rows = fetch_all(
            f"""SELECT m.* FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE c.shop_id = {ph}""",
            (shop_id,),
        )
    else:
        rows = fetch_all(f"SELECT * FROM {table}")

    if not rows:
        return ""  # Empty file in the zip — caller can still verify the table was attempted.

    buf = io.StringIO()
    cols = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Coerce non-serialisable values (datetime, dict from JSON columns) to strings.
        writer.writerow({c: ("" if row.get(c) is None else str(row.get(c))) for c in cols})
    return buf.getvalue()


def export_all_tables_zip(shop_id: int | None = None) -> tuple[bytes, str]:
    """Return (zip_bytes, filename). filename includes ISO date + optional shop_id.

    Per-shop scope is best-effort: tables without a shop_id column (like
    settings/migration tables) are skipped when shop_id is set.
    """
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in _PLATFORM_TABLES:
            if shop_id is not None and table not in (*_SHOP_SCOPED_TABLES, "shops", "messages"):
                continue
            try:
                csv_text = _dump_table_to_csv(table, shop_id)
            except Exception as e:
                log.exception("Backup of table %s failed", table)
                csv_text = f"# dump failed: {e}\n"
            zf.writestr(f"{table}.csv", csv_text)

        # Manifest for human eyes
        manifest = (
            f"Vendly backup\n"
            f"Generated: {datetime.utcnow().isoformat()}Z\n"
            f"Scope: {'shop_id=' + str(shop_id) if shop_id else 'full platform'}\n"
            f"Tables: {', '.join(_PLATFORM_TABLES)}\n"
        )
        zf.writestr("MANIFEST.txt", manifest)

    date_part = datetime.utcnow().strftime("%Y-%m-%d")
    scope_part = f"-shop{shop_id}" if shop_id else ""
    filename = f"vendly-backup-{date_part}{scope_part}.zip"
    return zip_buf.getvalue(), filename
