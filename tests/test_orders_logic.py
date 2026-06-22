"""Unit tests for pure order-flow helpers in orders.py."""
import orders


class TestLooksLikeOrderRequest:
    def test_triggers(self):
        assert orders.looks_like_order_request("хочу купить")
        assert orders.looks_like_order_request("Хочу заказать кроссовки 42")
        assert orders.looks_like_order_request("давайте оформить заказ")

    def test_non_triggers(self):
        assert not orders.looks_like_order_request("просто смотрю")
        assert not orders.looks_like_order_request("сколько стоит?")


class TestLooksLikePhone:
    def test_valid_phones(self):
        assert orders.looks_like_phone("+7 701 234 56 78")
        assert orders.looks_like_phone("87012345678")
        assert orders.looks_like_phone("+1 (234) 567-8901")

    def test_invalid_phones(self):
        assert not orders.looks_like_phone("12345")          # too short
        assert not orders.looks_like_phone("Иван")            # no digits
        assert not orders.looks_like_phone("1234567890123456")  # too long (16)


class TestNormalizeProductInterest:
    def test_strips_generic_trigger_prefix(self):
        assert orders._normalize_product_interest("хочу купить найк аир") == "найк аир"

    def test_generic_only_returns_empty(self):
        assert orders._normalize_product_interest("хочу купить") == ""
        assert orders._normalize_product_interest("оформить заказ") == ""

    def test_keeps_non_trigger_text(self):
        assert orders._normalize_product_interest("красные кроссовки") == "красные кроссовки"


class TestOrderStatusValidation:
    def test_known_statuses(self):
        assert "new" in orders.ORDER_STATUSES
        assert "cancelled" in orders.ORDER_STATUSES
