"""Pin the P3 item 3 fail-closed behaviour of shops.resolve_shop_id.

Before P3: resolve_shop_id(None) silently returned get_default_shop_id() —
any forgotten shop_id parameter would leak across tenants by routing the
request to the default shop. After P3: None raises ValueError, forcing every
caller to either thread a real shop_id or opt-in by calling get_default_shop_id
explicitly.
"""
import pytest

import shops


class TestResolveShopIdFailClosed:

    def test_none_raises_value_error(self):
        with pytest.raises(ValueError) as exc:
            shops.resolve_shop_id(None)
        assert "None" in str(exc.value)

    def test_no_arg_raises(self):
        with pytest.raises(ValueError):
            shops.resolve_shop_id()

    def test_int_passes_through(self):
        assert shops.resolve_shop_id(7) == 7

    def test_does_not_call_default_shop_lookup_on_int(self, monkeypatch):
        # Even if the default-shop helper exists, an explicit shop_id must never
        # trigger a DB roundtrip — only the legitimate explicit-default callers
        # in routes/api.py and telegram_bot.py call get_default_shop_id.
        def boom():
            raise AssertionError("get_default_shop_id must not run on explicit int")
        monkeypatch.setattr(shops, "get_default_shop_id", boom)
        assert shops.resolve_shop_id(42) == 42
