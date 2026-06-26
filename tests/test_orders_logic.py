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


class TestNormalizePhone:
    def test_kz_11_digits(self):
        assert orders.normalize_phone("87012345678") == "+77012345678"
        assert orders.normalize_phone("+7 701 234 56 78") == "+77012345678"

    def test_kz_10_digit_national(self):
        assert orders.normalize_phone("7012345678") == "+77012345678"

    def test_rejects_random_10_digits(self):
        # The transcript bug: a junk 10-digit number must not pass.
        assert orders.normalize_phone("5676678657") is None

    def test_rejects_non_phone(self):
        assert orders.normalize_phone("Иван") is None
        assert orders.normalize_phone("12345") is None


class TestIsValidName:
    def test_rejects_pure_digits(self):
        # The transcript bug: a phone number typed into the name slot.
        assert not orders._is_valid_name("87765645633")

    def test_rejects_too_short(self):
        assert not orders._is_valid_name("a")
        assert not orders._is_valid_name("")

    def test_accepts_real_names(self):
        assert orders._is_valid_name("Иван")
        assert orders._is_valid_name("Ali")


class TestConfirmCancel:
    def test_confirm_words(self):
        assert orders._is_confirm("да")
        assert orders._is_confirm("Да, верно")
        assert orders._is_confirm("оформляй")

    def test_cancel_words(self):
        assert orders._is_cancel("нет")
        assert orders._is_cancel("не надо")
        assert orders._is_cancel("отмена")

    def test_ambiguous_is_neither(self):
        assert not orders._is_confirm("привет")
        assert not orders._is_cancel("привет")
