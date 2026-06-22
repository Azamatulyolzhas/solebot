"""Tests for the DB backup module + /admin/backup endpoint.

We don't spin up a real DB — backup.fetch_all is monkeypatched to return
canned rows so the ZIP structure can be inspected without I/O.
"""
import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backup
from routes.admin import router as admin_router


class TestExportAllTablesZip:

    def test_full_export_contains_all_whitelisted_tables(self, monkeypatch):
        # fetch_all returns one canned row per table.
        def fake_fetch_all(query, params=()):
            # Each table query returns a different row shape; here we just stub
            # with a consistent dict so DictWriter works.
            if "FROM shops" in query and "WHERE id" not in query and "WHERE c.shop_id" not in query:
                return [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
            return [{"id": 1, "shop_id": 1, "col": "x"}]

        monkeypatch.setattr(backup, "fetch_all", fake_fetch_all)

        data, name = backup.export_all_tables_zip(shop_id=None)
        assert name.startswith("vendly-backup-") and name.endswith(".zip")
        assert "shop" not in name  # no shop-scope suffix on a full backup

        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        for table in backup._PLATFORM_TABLES:
            assert f"{table}.csv" in names, f"{table}.csv missing from backup zip"
        assert "MANIFEST.txt" in names

    def test_per_shop_scope_filters_filename_and_content(self, monkeypatch):
        captured_queries = []

        def fake_fetch_all(query, params=()):
            captured_queries.append((query.strip().split("\n")[0], params))
            return [{"id": 99, "shop_id": 7, "name": "Z"}]

        monkeypatch.setattr(backup, "fetch_all", fake_fetch_all)

        data, name = backup.export_all_tables_zip(shop_id=7)
        assert "shop7" in name

        # The shops table should be scoped to id=7 (not WHERE shop_id).
        shop_q = [q for q in captured_queries if "FROM shops" in q[0]]
        assert any(p == (7,) for _, p in shop_q)

        # The messages table should be joined via conversations.
        msg_q = [q for q in captured_queries if q[0].startswith("SELECT m.*")]
        assert msg_q, "messages backup must use JOIN against conversations"

    def test_empty_table_writes_empty_csv_not_error(self, monkeypatch):
        monkeypatch.setattr(backup, "fetch_all", lambda q, p=(): [])

        data, _ = backup.export_all_tables_zip(shop_id=None)
        zf = zipfile.ZipFile(io.BytesIO(data))
        # Pick one — it must exist and be empty (zero bytes).
        assert zf.read("shops.csv") == b""


class TestBackupEndpoint:

    def test_requires_admin(self, monkeypatch):
        app = FastAPI()
        app.include_router(admin_router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/admin/backup")
        # is_admin_configured False or no auth → 404 or 403; pin "not 200".
        assert resp.status_code != 200

    def test_returns_zip_for_admin(self, monkeypatch):
        # Bypass require_admin and stub the export.
        monkeypatch.setattr("routes.admin.require_admin", lambda req: None)
        monkeypatch.setattr(
            "backup.export_all_tables_zip",
            lambda shop_id=None: (b"PK\x03\x04fake-zip-bytes", "vendly-backup-2026-06-22.zip"),
        )

        app = FastAPI()
        app.include_router(admin_router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/admin/backup")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "vendly-backup-2026-06-22.zip" in resp.headers["content-disposition"]
        assert resp.content == b"PK\x03\x04fake-zip-bytes"
