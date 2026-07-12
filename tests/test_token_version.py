"""Password change must invalidate previously issued JWTs (token_version).

The chain under test:
  auth.create_shop_token(shop_id, token_version) puts a "tv" claim in the JWT;
  routes.shop.get_current_shop compares that claim against shops.token_version;
  shops.set_shop_owner_password bumps the version, killing old tokens.
"""
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import auth
import routes.shop as shop_route


def _legacy_token(shop_id: int) -> str:
    """A pre-versioning token: no "tv" claim at all."""
    payload = {
        "sub": str(shop_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
    }
    return pyjwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)


def _make_client() -> TestClient:
    """Minimal app: the shop router plus one endpoint behind get_current_shop."""
    app = FastAPI()
    app.include_router(shop_route.router)

    @app.get("/protected")
    def protected(shop: dict = Depends(shop_route.get_current_shop)):
        return {"shop_id": shop["id"]}

    return TestClient(app, raise_server_exceptions=False)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestClaims:
    def test_round_trip_with_version(self):
        token = auth.create_shop_token(42, 7)
        assert auth.decode_shop_claims(token) == (42, 7)
        # Old-style helper keeps returning the shop id.
        assert auth.decode_shop_token(token) == 42

    def test_legacy_token_counts_as_version_zero(self):
        assert auth.decode_shop_claims(_legacy_token(42)) == (42, 0)

    def test_admin_token_is_not_shop_claims(self):
        assert auth.decode_shop_claims(auth.create_admin_token()) is None


class TestGetCurrentShop:
    @pytest.fixture()
    def shop_row(self, monkeypatch):
        row = {
            "id": 5, "name": "Shop", "slug": "shop",
            "owner_email": "owner@example.com",
            "status": "active", "token_version": 0,
        }
        monkeypatch.setattr(
            shop_route, "get_shop_by_id",
            lambda shop_id: row if shop_id == 5 else None,
        )
        return row

    def test_current_version_accepted(self, shop_row):
        client = _make_client()
        token = auth.create_shop_token(5, 0)
        assert client.get("/protected", headers=_auth(token)).status_code == 200

    def test_stale_token_rejected_after_version_bump(self, shop_row):
        client = _make_client()
        old = auth.create_shop_token(5, 0)
        shop_row["token_version"] = 1  # password change happened
        assert client.get("/protected", headers=_auth(old)).status_code == 401
        fresh = auth.create_shop_token(5, 1)
        assert client.get("/protected", headers=_auth(fresh)).status_code == 200

    def test_legacy_unversioned_token_valid_until_bump(self, shop_row):
        client = _make_client()
        legacy = _legacy_token(5)
        assert client.get("/protected", headers=_auth(legacy)).status_code == 200
        shop_row["token_version"] = 1
        assert client.get("/protected", headers=_auth(legacy)).status_code == 401


class TestChangePasswordFlow:
    def test_password_change_kills_old_token_and_returns_fresh_one(self, monkeypatch):
        db = {
            "id": 5, "name": "Shop", "slug": "shop",
            "owner_email": "owner@example.com",
            "status": "active", "token_version": 0,
            "owner_password_hash": "hash:oldpass123",
        }

        def fake_set_password(shop_id, password_hash):
            db["owner_password_hash"] = password_hash
            db["token_version"] += 1

        monkeypatch.setattr(shop_route, "get_shop_by_id",
                            lambda sid: db if sid == 5 else None)
        monkeypatch.setattr(shop_route, "get_shop_by_email", lambda email: db)
        monkeypatch.setattr(shop_route, "set_shop_owner_password", fake_set_password)
        monkeypatch.setattr(shop_route, "hash_password", lambda p: "hash:" + p)
        monkeypatch.setattr(shop_route, "verify_password",
                            lambda p, h: h == "hash:" + p)

        client = _make_client()
        old_token = auth.create_shop_token(5, 0)
        assert client.get("/protected", headers=_auth(old_token)).status_code == 200

        resp = client.post(
            "/shop/change-password", headers=_auth(old_token),
            json={"current_password": "oldpass123", "new_password": "newpass456"},
        )
        assert resp.status_code == 200
        new_token = resp.json()["token"]

        # The token used before the change is dead; the reissued one works.
        assert client.get("/protected", headers=_auth(old_token)).status_code == 401
        assert client.get("/protected", headers=_auth(new_token)).status_code == 200


class TestSqliteIntegration:
    def test_set_shop_owner_password_bumps_version_in_real_db(self, tmp_path, monkeypatch):
        """End-to-end against a real SQLite file: boot DDL creates the
        token_version column and the UPDATE actually increments it."""
        import db as db_module
        import schema
        import shops

        monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "t.db"))
        schema.ensure_app_tables()

        from db import execute_write, fetch_all

        execute_write(
            "INSERT INTO shops (name, slug, owner_email, owner_password_hash) "
            "VALUES (?, ?, ?, ?)",
            ("Shop", "shop", "owner@example.com", "hash:old"),
        )
        shop_id = fetch_all("SELECT id FROM shops WHERE slug = ?", ("shop",))[0]["id"]

        before = shops.get_shop_by_id(shop_id)
        assert int(before["token_version"] or 0) == 0

        shops.set_shop_owner_password(shop_id, "hash:new")

        after = shops.get_shop_by_id(shop_id)
        assert int(after["token_version"]) == 1
        assert shops.get_shop_by_email("owner@example.com")["token_version"] == 1
