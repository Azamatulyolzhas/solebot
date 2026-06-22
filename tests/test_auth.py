"""Unit tests for auth.py — password hashing and JWT issue/verify.

Security-critical: a regression here either locks everyone out or lets the
wrong principal in. Tokens are tested for round-trip AND for cross-role
rejection (a shop token must never pass as admin, and vice-versa).
"""
import auth


class TestPasswordHashing:
    def test_hash_then_verify(self):
        h = auth.hash_password("S3cret-pass")
        assert h != "S3cret-pass"           # never store plaintext
        assert auth.verify_password("S3cret-pass", h)

    def test_wrong_password_fails(self):
        h = auth.hash_password("S3cret-pass")
        assert not auth.verify_password("wrong", h)

    def test_verify_handles_garbage_hash(self):
        assert not auth.verify_password("x", "not-a-bcrypt-hash")


class TestShopToken:
    def test_round_trip(self):
        token = auth.create_shop_token(42)
        assert auth.decode_shop_token(token) == 42

    def test_invalid_token_returns_none(self):
        assert auth.decode_shop_token("garbage.token.value") is None
        assert auth.decode_shop_token("") is None


class TestAdminToken:
    def test_admin_token_validates_as_admin(self):
        token = auth.create_admin_token()
        assert auth.decode_admin_token(token) is True

    def test_shop_token_is_not_admin(self):
        shop_token = auth.create_shop_token(1)
        assert auth.decode_admin_token(shop_token) is False

    def test_admin_token_is_not_a_shop(self):
        # An admin token must not resolve to a shop_id (privilege separation).
        admin_token = auth.create_admin_token()
        assert auth.decode_shop_token(admin_token) is None
