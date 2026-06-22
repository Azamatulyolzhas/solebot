"""Verify the analytics_events composite index is created by ensure_app_tables.

P3 item 2: COUNT(*) on analytics_events runs 2x per Telegram message inside
ai.ask_ai's quota check. Without an index this is a full scan over an
append-only table. The index covers (shop_id, event_name, created_at) which
is the literal predicate of the count query.
"""
import os
import sqlite3
import tempfile

import schema


class TestAnalyticsIndex:

    def test_composite_index_created(self, monkeypatch):
        # Run ensure_app_tables against a fresh SQLite DB and verify the index.
        tmpfd, tmppath = tempfile.mkstemp(suffix=".db")
        os.close(tmpfd)
        try:
            monkeypatch.setattr(schema, "USE_POSTGRES", False)
            import db as _db
            monkeypatch.setattr(_db, "DB_PATH", tmppath)
            monkeypatch.setattr(_db, "USE_POSTGRES", False)

            schema.ensure_app_tables()

            conn = sqlite3.connect(tmppath)
            try:
                rows = conn.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='analytics_events'"
                ).fetchall()
            finally:
                conn.close()
            names = [r[0] for r in rows]
            assert "idx_analytics_events_shop_event_time" in names, (
                f"Expected analytics_events composite index, got: {names}"
            )
            # And it must reference all three columns.
            sql = next(r[1] for r in rows if r[0] == "idx_analytics_events_shop_event_time")
            for col in ("shop_id", "event_name", "created_at"):
                assert col in sql
        finally:
            try:
                os.unlink(tmppath)
            except OSError:
                pass
